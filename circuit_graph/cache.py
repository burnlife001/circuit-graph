"""IR parse/enrich cache.

The full parse + enricher pipeline on a large Protel netlist costs ~1 s; a
cache keyed by (netlist content, enricher version, primitives library hash)
makes repeated ``cg view`` / ``cg export`` calls on an unchanged board
effectively free.

Cache files live in ``<repo>/.cg-cache/`` by default (git-ignored); every CLI
command that parses a netlist accepts ``--cache-dir`` to override the location
and ``--no-cache`` to bypass.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from circuit_graph.ir import CircuitIR, ir_from_dict, ir_to_dict

# Bump when the enricher pipeline semantics change (new enricher, changed
# defaults, new warnings worth surfacing).  Part of the cache key.
# v3: NetClassifier 不再把 CGND 并入 GND（crash.md T1），EdgeSemanticEnricher
#     对 UNKNOWN 地网按名字回退 role=gnd —— 旧缓存里的 CGND 分类已过时。
ENRICHER_VERSION = "3"

_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cg-cache"


def default_cache_dir() -> Path:
    return _DEFAULT_CACHE_DIR


def primitives_fingerprint(primitives_dir: Path | None = None) -> str:
    """Hash the primitive library: sorted (relpath, sha256) pairs."""
    base = Path(primitives_dir) if primitives_dir else (
        Path(__file__).resolve().parent.parent / "primitives"
    )
    h = hashlib.sha256()
    if base.exists():
        for path in sorted(base.rglob("*.json")):
            rel = path.relative_to(base).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def pipeline_fingerprint(primitives_dir: Path | None = None) -> str:
    """Cache-key component covering code + primitive-library versions."""
    return f"enrichers-v{ENRICHER_VERSION}:prims-{primitives_fingerprint(primitives_dir)}"


def cache_key(netlist_path: Path | str, primitives_dir: Path | None = None) -> str:
    """Content-addressed cache key for a netlist file."""
    data = Path(netlist_path).read_bytes()
    h = hashlib.sha256()
    h.update(data)
    h.update(pipeline_fingerprint(primitives_dir).encode("utf-8"))
    return h.hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def load_cached(
    cache_dir: Path, key: str
) -> tuple[CircuitIR, list[str]] | None:
    """Return (ir, warnings) when the cache holds ``key``, else None."""
    path = _cache_path(cache_dir, key)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return ir_from_dict(doc["ir"]), list(doc.get("warnings", []))
    except (OSError, ValueError, KeyError):
        return None


def save_cached(cache_dir: Path, key: str, ir: CircuitIR, warnings: list[str]) -> Path:
    """Write ``ir`` + ``warnings`` under ``cache_dir`` and return the file path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, key)
    doc = {
        "key": key,
        "saved_at": time.time(),
        "warnings": list(warnings),
        "ir": ir_to_dict(ir),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic on same filesystem
    return path


def stats(cache_dir: Path) -> dict[str, Any]:
    """Cache directory stats for diagnostics/tests."""
    files = list(Path(cache_dir).glob("*.json")) if Path(cache_dir).exists() else []
    return {"entries": len(files), "bytes": sum(f.stat().st_size for f in files)}
