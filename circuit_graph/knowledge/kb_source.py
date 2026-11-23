"""KB 补全来源 —— 把缺失的引脚数据从**显式来源**拉进 KB。

三种来源（由 ``cg role --kb-source`` 指定）：

============  ==========================================================
来源          链路
============  ==========================================================
``pin2json``  现有 ``circuit_graph.knowledge.chip_resolver`` 链路
              （JLCPCB 检索 + JLC2KiCadLib 生成符号 → 解析引脚名）
``datasheet`` 现有本地 datasheet PDF 查找 + ``pdf_pin_extract`` 提取链路
``http(s)://`` 把 URL 当作**单个** datasheet PDF：下载到缓存后复用同一条
              确定性 PDF 提取链路
============  ==========================================================

原则（与 ``cg role`` 的 CLI 合同一致）：

* **不传来源 = 严格离线**：只读现有 KB，不联网、不调外部 CLI。
* **显式来源不暗中回退**：pin2json 失败不会偷偷改用 datasheet；
  失败就如实报告，不猜引脚。
* **精确型号 / 封装校验**：解析出的封装与板上 footprint 不一致时**不写入**，
  按失败报告，避免把别的变体的引脚名安到板上。板上实际接出的引脚必须是
  来源 pinout 的子集——这一条与 package 是否存在无关（否则 8 脚 PDF 可以
  写给 32 脚器件）。URL datasheet 还必须能从 URL 文件名或 PDF 内容核验
  目标型号，核验不了就拒绝入库（仅有 ``%PDF`` 头不算数）。
* **复用现有存储与 provenance**：写入走 ``ComponentDatabase`` 既有的
  pin2json 写入路径，不新建第二套 KB。
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from circuit_graph.ir import Component

#: ``--kb-source`` 允许的具名来源
NAMED_SOURCES = ("pin2json", "datasheet")

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

#: 下载超时（秒）
DOWNLOAD_TIMEOUT = 60


class KbSourceError(Exception):
    """来源不可用或解析失败（消息会原样报告给用户）。"""


@dataclass(frozen=True)
class KbSource:
    """``--kb-source`` 的取值：具名来源或 datasheet PDF 的 URL。"""

    kind: str  # "pin2json" | "datasheet" | "url"
    url: str | None = None

    @classmethod
    def parse(cls, raw: str) -> "KbSource":
        """解析 CLI 传入的 ``--kb-source`` 字符串。"""
        value = (raw or "").strip()
        if not value:
            raise KbSourceError("--kb-source 不能为空")
        if _URL_RE.match(value):
            return cls(kind="url", url=value)
        lowered = value.lower()
        if lowered not in NAMED_SOURCES:
            raise KbSourceError(
                f"未知的 --kb-source: {raw!r}"
                f"（可用：{', '.join(NAMED_SOURCES)} 或 http(s)://… 的 datasheet PDF）"
            )
        return cls(kind=lowered)

    @property
    def is_url(self) -> bool:
        return self.kind == "url"

    def describe(self) -> str:
        return self.url if self.is_url else self.kind


@dataclass
class ResolvedPins:
    """一次来源解析的结果。"""

    pins: dict[str, str]
    pin_source: str
    package: str | None = None
    #: 器件事实（类别 / 描述 / 厂商）——KB 存"它是什么"
    facts: dict[str, str] = field(default_factory=dict)


@dataclass
class FillReport:
    """一次 KB 补全的结果汇总。"""

    source: str
    resolved: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    #: 没有可补全目标等情况下的说明（正常补全时为 None）
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source": self.source,
            "resolved": self.resolved,
            "failed": self.failed,
            "skipped": self.skipped,
        }
        if self.note:
            out["note"] = self.note
        return out

    def summary(self) -> str:
        text = (
            f"KB 补全（{self.source}）：成功 {len(self.resolved)}，"
            f"失败 {len(self.failed)}，跳过 {len(self.skipped)}"
        )
        if self.note:
            text += f"\n  = {self.note}"
        return text


# ---------------------------------------------------------------------------
# 单个元件的解析
# ---------------------------------------------------------------------------


def _part_number(comp: Component) -> str | None:
    """元件用于查 KB 的型号（精确型号优先，其次原值）。"""
    for candidate in (comp.part_number, comp.raw_value):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def resolve_pins_for_component(
    source: KbSource,
    comp: Component,
    *,
    cache_dir: Path | None = None,
) -> ResolvedPins:
    """按来源解析单个元件的引脚名。

    失败抛 :class:`KbSourceError`。
    """
    part = _part_number(comp)
    if not part:
        raise KbSourceError("该元件没有型号，无法解析")

    if source.kind == "pin2json":
        return _resolve_via_pin2json(part, comp)
    if source.kind == "datasheet":
        return _resolve_via_datasheet(part, comp)
    if source.kind == "url":
        assert source.url is not None
        return _resolve_via_url(source.url, part, comp, cache_dir=cache_dir)
    raise KbSourceError(f"未知的来源类型: {source.kind}")


def _resolve_via_pin2json(part: str, comp: Component) -> ResolvedPins:
    """走现有 chip_resolver 链路（JLCPCB 符号生成）。"""
    from circuit_graph.knowledge import chip_resolver

    payload = chip_resolver.resolve_part(part, package=comp.footprint)
    if not payload or not payload.get("pins"):
        raise KbSourceError(f"pin2json 未解析出引脚: {part}")
    facts = {
        key: str(payload[key])
        for key in ("category", "description", "manufacturer")
        if payload.get(key)
    }
    return ResolvedPins(
        pins=dict(payload["pins"]),
        pin_source="jlcpcb",
        package=payload.get("package") or None,
        facts=facts,
    )


def _resolve_via_datasheet(part: str, comp: Component) -> ResolvedPins:
    """走现有本地 datasheet PDF 查找 + pdf_pin_extract 提取链路。"""
    from circuit_graph.knowledge.pdf_pin_extract import extract_from_datasheet

    pin_map = extract_from_datasheet(part)
    if not pin_map:
        raise KbSourceError(f"未找到 {part} 的 datasheet 引脚表")
    return ResolvedPins(pins=dict(pin_map), pin_source="datasheet")


def _resolve_via_url(
    url: str,
    part: str,
    comp: Component,
    *,
    cache_dir: Path | None,
) -> ResolvedPins:
    """下载 datasheet PDF 到缓存 → 复用同一条确定性 PDF 提取链路。

    入库前必须先核验这份 PDF 确实属于 ``part``（URL 文件名或 PDF 内容里能
    找到该型号）；核验不了就拒绝——只凭 ``%PDF`` 头不足以信任文件。
    """
    from circuit_graph.knowledge.pdf_pin_extract import extract_pin_table, search_pin_pages

    pdf = download_datasheet(url, cache_dir=cache_dir)
    if not _confirms_part(url, pdf, part):
        raise KbSourceError(
            f"无法核验 {url} 的 datasheet 属于 {part}"
            "（URL 文件名与 PDF 内容里都找不到该型号），不写入"
        )
    pages = search_pin_pages(pdf)
    if not pages:
        pages = [1]
    pin_map = extract_pin_table(pdf, pages)
    if not pin_map:
        raise KbSourceError(f"从 {url} 的 PDF 里没解析出引脚表")
    return ResolvedPins(pins=dict(pin_map), pin_source="datasheet_url")


def _normalized_token(text: str) -> str:
    """型号/文件名的归一化形态：去掉分隔符，统一小写。"""
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def _confirms_part(url: str, pdf: Path, part: str) -> bool:
    """核验 datasheet 属于 ``part``：URL 文件名或 PDF 内容二选一能对上。"""
    from urllib.parse import unquote, urlparse

    filename = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    if _normalized_token(part) and _normalized_token(part) in _normalized_token(filename):
        return True
    return _pdf_contains(pdf, part) is True


def _pdf_contains(pdf: Path, part: str, *, timeout: int = 30) -> bool | None:
    """PDF 文本里能否找到 ``part``；无法判定（无提取环境 / 提取失败）返回 None。

    与 ``pdf_pin_extract.search_pin_pages`` 同一套思路：借 camelot venv 里的
    pypdfium2 抽文本，但只做"型号是否出现"这一件事。
    """
    from circuit_graph.config import camelot_venv

    venv = camelot_venv()
    if venv is None:
        return None
    py_exe = venv / "Scripts" / "python"
    script = (
        "import sys\n"
        "from pypdfium2 import PdfDocument\n"
        "doc = PdfDocument(sys.argv[1])\n"
        "needle = sys.argv[2].lower()\n"
        "found = 'no'\n"
        "for i in range(len(doc)):\n"
        "    text = doc[i].get_textpage().get_text_range() or ''\n"
        "    if needle in text.lower():\n"
        "        found = 'yes'\n"
        "        break\n"
        "print(found)\n"
    )
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(  # noqa: S603
            [str(py_exe), script_path, str(pdf), part],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        Path(script_path).unlink(missing_ok=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip().endswith("yes")


def download_datasheet(url: str, *, cache_dir: Path | None = None) -> Path:
    """把 URL 指向的 datasheet PDF 下载到缓存目录，返回本地路径。

    已缓存（同一 URL）时直接复用，不重复下载。
    """
    root = Path(cache_dir) if cache_dir else Path(".cg-cache")
    base = root / "datasheets"
    base.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    target = base / f"{digest}.pdf"
    if target.exists() and target.stat().st_size > 0:
        return target

    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
            data = resp.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise KbSourceError(f"下载 datasheet 失败: {exc}") from exc

    if not data.startswith(b"%PDF"):
        raise KbSourceError("下载到的不是 PDF 文件（缺少 %PDF 头）")
    target.write_bytes(data)
    return target


# ---------------------------------------------------------------------------
# 批量补全
# ---------------------------------------------------------------------------


def _is_fillable(comp: Component) -> bool:
    """是否值得补 KB 引脚：有型号的非被动器件。"""
    if comp.device_kind.value in ("Resistor", "Capacitor", "Inductor"):
        return False
    return bool(_part_number(comp))


def fill_missing_pins(
    db: Any,
    components: list[Component],
    source: KbSource,
    *,
    cache_dir: Path | None = None,
) -> FillReport:
    """把 ``components`` 里 KB 缺失的引脚按 ``source`` 补进 KB。

    * 只补 **KB 里没有** 的型号；已有引脚数据的元件跳过。
    * 单个元件解析失败不影响其他元件（失败记进 ``failed``）。
    * 封装与板上 footprint 不一致时**不写入**，按失败报告。
    """
    report = FillReport(source=source.describe())

    for comp in components:
        part = _part_number(comp)
        if not part:
            report.skipped.append(
                {"designator": comp.id, "reason": "没有型号（part number 与原值均为空）"}
            )
            continue
        if not _is_fillable(comp):
            report.skipped.append(
                {"designator": comp.id, "part_number": part, "reason": "被动器件，不需要引脚名"}
            )
            continue
        if db.get_pinout(part) is not None:
            report.skipped.append(
                {"designator": comp.id, "part_number": part, "reason": "KB 已有该型号的引脚数据"}
            )
            continue

        try:
            resolved = resolve_pins_for_component(
                source, comp, cache_dir=cache_dir
            )
        except KbSourceError as exc:
            report.failed.append(
                {"designator": comp.id, "part_number": part, "reason": str(exc)}
            )
            continue
        except Exception as exc:  # noqa: BLE001 —— 外部链路异常不许中断整批
            report.failed.append(
                {
                    "designator": comp.id,
                    "part_number": part,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        mismatch = _package_mismatch(comp, resolved)
        if mismatch is not None:
            report.failed.append(
                {"designator": comp.id, "part_number": part, "reason": mismatch}
            )
            continue

        db.upsert_resolved_pins(
            part,
            resolved.pins,
            pin_source=resolved.pin_source,
            board_package=comp.footprint,
        )
        if resolved.facts:
            db.set_component_facts(part, **resolved.facts)
        row = {
            "designator": comp.id,
            "part_number": part,
            "pin_source": resolved.pin_source,
            "pin_count": len(resolved.pins),
        }
        if resolved.package:
            row["package"] = resolved.package
        if mismatch is None and _package_warns(comp, resolved):
            row["warning"] = _package_warns(comp, resolved)
        report.resolved.append(row)
    return report


def _package_warns(comp: Component, resolved: ResolvedPins) -> str | None:
    """封装名对不上但引脚数一致时给个提示（不阻断写入）。"""
    if not resolved.package or not comp.footprint:
        return None
    from circuit_graph.knowledge.package_map import packages_match

    if packages_match(comp.footprint, resolved.package):
        return None
    if comp.pins and len(comp.pins) == len(resolved.pins):
        return (
            f"封装名不一致（板上 footprint={comp.footprint}，来源={resolved.package}），"
            f"但引脚数一致（{len(resolved.pins)}），按同型号处理"
        )
    return None


def _package_mismatch(comp: Component, resolved: ResolvedPins) -> str | None:
    """来源引脚/封装与板对不上时返回原因字符串；可以接受时返回 None。

    核验次序（第 1 条与 package 是否存在**无关**——否则"8 脚 PDF 写给
    32 脚器件"会在 package 缺失时完全漏网）：

    1. 板上实际接出的引脚必须是来源 pinout 的子集：来源连板上在用的脚都
       给不出，就不可能是同一器件。
    2. 封装名两边都有时用 ``packages_match`` 比对；写法不同但板上把来源
       pinout 的脚全接齐（数量一致）也接受为同一器件。
    """
    board_pins = [str(p.number) for p in (comp.pins or [])]
    resolved_numbers = set(resolved.pins)
    if board_pins:
        missing = [n for n in board_pins if n not in resolved_numbers]
        if missing or len(resolved.pins) < len(board_pins):
            shown = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
            detail = f"，来源缺少板上接出的引脚 {shown}" if missing else ""
            return (
                f"来源引脚对不上板上实际接出的引脚（板上 {len(board_pins)} pins，"
                f"来源 {len(resolved.pins)} pins{detail}），不写入以免安错引脚名"
            )

    if not resolved.package or not comp.footprint:
        return None
    from circuit_graph.knowledge.package_map import packages_match

    if packages_match(comp.footprint, resolved.package):
        return None
    if comp.pins and len(comp.pins) == len(resolved.pins):
        return None  # 封装写法不同但引脚数一致 → 同一器件
    return (
        f"封装/引脚数对不上（板上 footprint={comp.footprint}、{len(comp.pins)} pins，"
        f"来源={resolved.package}、{len(resolved.pins)} pins），不写入以免安错引脚名"
    )


def target_components(
    ir: Any,
    designator: str | None,
    *,
    all_components: bool,
) -> list[Component]:
    """选出本次要补 KB 的元件。

    * ``--all``：补板内所有**缺引脚数据的非被动器件**（批量场景，明确要求
      逐个去解析）。
    * 单位号查询：**只补目标本身**。目标是非被动且有型号的器件才补它；
      目标是电阻/电容等被动件或没有型号时返回空列表——被动件没有引脚名
      可补，也不得为它偷偷联网去抓相邻 IC（那是把"单位号查询"变成
      "批量补全"，违背用户合同）。
    """
    comps = list(ir.components)
    if all_components:
        return [c for c in comps if _is_fillable(c)]
    if not designator:
        return []
    wanted = designator.strip().lower()
    focal = next((c for c in comps if c.id.lower() == wanted), None)
    if focal is None or not _is_fillable(focal):
        return []
    return [focal]


__all__ = [
    "DOWNLOAD_TIMEOUT",
    "FillReport",
    "KbSource",
    "KbSourceError",
    "NAMED_SOURCES",
    "download_datasheet",
    "fill_missing_pins",
    "resolve_pins_for_component",
    "target_components",
]
