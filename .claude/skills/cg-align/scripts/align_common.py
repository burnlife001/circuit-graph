"""cg-align 三个脚本共用的确定性工具（无 LLM、无网络）。

只做四件事：
1. 以绝对路径调 ``cg``（避免 Windows Win32 相对路径坑）并解析 JSON；
2. 读/写 ``*.answers.jsonl``（基准层 oracle）；
3. 定位仓库根、primitives 模板目录与已知模板 id；
4. UTF-8 输出（绕过 GBK 控制台）。

约定：所有命令都经 ``cg`` 取电路数据，**不读 .NET 原文**。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# .claude/skills/cg-align/scripts/align_common.py → 上溯 4 层是仓库根
REPO_ROOT = Path(__file__).resolve().parents[4]
PRIMITIVES_DIR = REPO_ROOT / "primitives"
FIXTURES_DIR = REPO_ROOT / "fixtures" / "protel"

# cg 入口：优先 venv 里的绝对路径 exe；找不到则回退 ``python -m circuit_graph.cli``
_CG_EXE = REPO_ROOT / ".venv" / "Scripts" / "cg.exe"

# 权威来源优先级：human > session-correction > cg-role
SOURCE_PRIORITY = {"cg-role": 0, "session-correction": 1, "human": 2}
VALID_SOURCES = tuple(SOURCE_PRIORITY)

# answers.jsonl 里 block 字段的“无模板”占位
UNMATCHED_BLOCK = "(unmatched)"


# --------------------------------------------------------------------------- 输出


def emit(text: str) -> None:
    """UTF-8 写到 stdout（Windows 控制台默认 GBK 会炸中文）。

    管道被下游关闭（head/grep 提前退出）时静默收尾，不抛 BrokenPipeError。
    """
    try:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    except OSError:
        pass


def fail(text: str, code: int = 2) -> None:
    """错误信息走 stderr。"""
    sys.stderr.buffer.write((text.rstrip("\n") + "\n").encode("utf-8"))
    sys.stderr.buffer.flush()
    raise SystemExit(code)


# --------------------------------------------------------------------------- cg 调用


def cg_command() -> list[str]:
    if _CG_EXE.is_file():
        return [str(_CG_EXE)]
    return [sys.executable, "-m", "circuit_graph.cli"]


def run_cg(args: list[str]) -> object:
    """跑一条 cg 命令并返回解析后的 JSON。

    失败时抛 SystemExit(2)，错误信息带上 stderr 原文。
    """
    cmd = cg_command() + args
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        env=env,
    )
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        fail(f"cg 命令失败 ({' '.join(args)}): {err or out or '无输出'}")
    if not out:
        fail(f"cg 命令无输出 ({' '.join(args)}): {err}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        fail(f"cg 输出不是合法 JSON ({' '.join(args)}): {exc}\n{out[:500]}")


def cg_query_all(net: Path) -> list[dict]:
    """``cg query NET --all --format json`` → 元件摘要列表。"""
    payload = run_cg(["query", net.as_posix(), "--all", "--format", "json"])
    sections = payload.get("sections", []) if isinstance(payload, dict) else []
    for section in sections:
        if section.get("query") == "all":
            return list(section.get("components", []))
    return []


def cg_query_comps(net: Path, designators: list[str]) -> list[dict]:
    """``cg query NET --comp A --comp B --format json`` → 每个元件的引脚→网络。"""
    args = ["query", net.as_posix()]
    for d in designators:
        args += ["--comp", d]
    args += ["--format", "json"]
    payload = run_cg(args)
    sections = payload.get("sections", []) if isinstance(payload, dict) else []
    return [s for s in sections if s.get("query") == "comp"]


def cg_role_all(net: Path) -> list[dict]:
    """``cg role --all NET --format json`` → 全量定性结果。"""
    payload = run_cg(["role", "--all", net.as_posix(), "--format", "json"])
    if not isinstance(payload, dict):
        return []
    return list(payload.get("results", []))


def cg_coverage(net: Path) -> dict:
    """``cg primitives coverage NET --format json`` → 覆盖度报告。"""
    payload = run_cg(["primitives", "coverage", net.as_posix(), "--format", "json"])
    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------- answers.jsonl


def default_answers_path(net: Path) -> Path:
    """``<net 同目录>/<stem>.answers.jsonl``；不存在时回退 fixtures/protel/。

    找不到已有文件时返回首选路径（写回时会在该位置新建）。
    """
    stem = net.stem
    candidate = net.parent / f"{stem}.answers.jsonl"
    if candidate.is_file():
        return candidate
    fallback = FIXTURES_DIR / f"{stem}.answers.jsonl"
    if fallback.is_file():
        return fallback
    return candidate


def read_answers(path: Path) -> list[dict]:
    """读 jsonl；跳过空行。行内不是对象则原样保留（由调用方处理）。"""
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"__malformed__": True, "__lineno__": lineno, "__raw__": line})
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                rows.append({"__malformed__": True, "__lineno__": lineno, "__raw__": line})
    return rows


def answers_by_designator(rows: list[dict]) -> dict[str, dict]:
    """designator → 行；同位号多行时后出现的覆盖先出现的。"""
    table: dict[str, dict] = {}
    for row in rows:
        if row.get("__malformed__"):
            continue
        desig = row.get("designator")
        if isinstance(desig, str) and desig.strip():
            table[desig.strip()] = row
    return table


def write_answers_atomic(path: Path, rows: list[dict]) -> Path:
    """先写 .bak 备份，再原子替换；返回备份路径。"""
    backup = path.with_suffix(path.suffix + ".bak")
    if path.is_file():
        backup.write_bytes(path.read_bytes())
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return backup


# --------------------------------------------------------------------------- primitives


def template_ids() -> set[str]:
    """primitives/ 下所有模板的 id 集合（按 id 字段，不是文件名）。"""
    ids: set[str] = set()
    if not PRIMITIVES_DIR.is_dir():
        return ids
    for file in PRIMITIVES_DIR.rglob("*.json"):
        if file.name == "schema.json":
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tid = data.get("id")
        if isinstance(tid, str) and tid:
            ids.add(tid)
    return ids


def load_templates() -> list[dict]:
    """读全部模板（带 file 字段），跳过 schema.json。"""
    out: list[dict] = []
    if not PRIMITIVES_DIR.is_dir():
        return out
    for file in sorted(PRIMITIVES_DIR.rglob("*.json")):
        if file.name == "schema.json":
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("id"):
            data = dict(data)
            data["__file__"] = file.relative_to(REPO_ROOT).as_posix()
            out.append(data)
    return out


# --------------------------------------------------------------------------- 小工具


def natural_key(text: str) -> tuple:
    """位号自然序：R2 < R10。"""
    import re

    parts = [p for p in re.split(r"(\d+)", text) if p != ""]
    return tuple((1, int(p), "") if p.isdigit() else (0, 0, p) for p in parts)


def normalize_text(text: str | None) -> str:
    """比较用的文本归一：去首尾空白、压缩内部空白。"""
    return " ".join((text or "").split())


def strip_role_suffix(text: str | None) -> str:
    """去掉 title 尾部的 " · Rf" 式角色后缀。

    cg role 的 title 形如 "同相反馈对（负反馈放大器） · Rf"，而 answers.jsonl 的
    function 字段入库时已剥掉后缀；比较两边前都要剥，否则全是假 mismatch。
    """
    import re

    return re.sub(r"\s*·\s*\S+$", "", (text or "").strip())


def unmatched_strip_note_required(old_block: str | None, source_note: str | None, known_ids) -> bool:
    """block_hint 把既有模板归属改成 (unmatched) 时是否必须提供 source_note。

    只在"旧 block 是已知模板 id"（即将清除一个仍然有效的归属）时要求依据；
    旧 block 为空 / 已是 (unmatched) / 引用了已删除的模板 id 时，不写依据也放行。
    防止 agent 用 (unmatched) 静默抹掉正确归属——下一次 block_drift 能发现，
    但写入当下就该被 challenged。
    """
    old = (old_block or "").strip()
    if not old or old == UNMATCHED_BLOCK:
        return False
    if old not in known_ids:
        return False
    return not (source_note or "").strip()


def resolve_block_hint(hint: str | None, known_ids) -> str | None:
    """block_hint → 合法 block 值；非法（未知模板 id / 空）返回 None。

    已知模板 id 可用；UNMATCHED_BLOCK ("(unmatched)") 也是显式合法值——
    表示"该答案确认无对应 primitives 模板"（如 R2 走 shunt 规则路径，
    基准如实记录未命中，而不是留着旧模板的错误归属）。
    """
    h = (hint or "").strip()
    if not h:
        return None
    if h == UNMATCHED_BLOCK:
        return UNMATCHED_BLOCK
    return h if h in known_ids else None


def classify_alignment(
    base_function: str | None,
    base_block: str | None,
    current_title: str | None,
    current_template_id: str | None,
) -> str:
    """把一条基准-当前对归类为 aligned / refined / mismatched。

    - aligned：两边剥掉 title 尾部 " · 角色" 后缀后文本相同；
    - refined：文本不同，但基准 block 与当前模板 id 相同（同一模板/功能，
      当前只是补充网络、配对元件、型号、参数等证据）。只认模板 id 这一
      强结构证据，不以文本包含关系认定语义相同；
    - mismatched：其余全部——block 不同（功能换了模板，如 R2 下拉→采样）、
      任一侧 block 未知（(unmatched) / None / 空串）。

    refined 是"待 agent 复核"的归类而非终审：报告须把 refined 逐条列出，
    由 agent 依据模板语义与用户答案确认，防止同模板被改语义后漏网。
    """
    base_fn = normalize_text(strip_role_suffix(base_function))
    cur_fn = normalize_text(strip_role_suffix(current_title))
    if base_fn == cur_fn:
        return "aligned"
    base_tid = (base_block or "").strip()
    cur_tid = (current_template_id or "").strip()
    if (
        base_tid
        and cur_tid
        and base_tid != UNMATCHED_BLOCK
        and cur_tid != UNMATCHED_BLOCK
        and base_tid == cur_tid
    ):
        return "refined"
    return "mismatched"


def template_of_instance(instance: str, ids) -> str | None:
    """从 block 实例 id 解出模板 id：最长前缀，且前缀后必须是 '-' 或结尾。

    与 ``cg primitives coverage`` 的解析规则一致（"rc-snubber" 不得吃掉
    "rc-snubber-series-x"）。
    """
    best: str | None = None
    for tid in ids:
        if not instance.startswith(tid):
            continue
        rest = instance[len(tid):]
        if rest and not rest.startswith("-"):
            continue
        if best is None or len(tid) > len(best):
            best = tid
    return best


def canonical_designator(designator: str, known: dict[str, dict]) -> str | None:
    """位号大小写不敏感地匹配到网表里的真实位号；找不到返回 None。"""
    desig = designator.strip()
    if desig in known:
        return desig
    lowered = desig.lower()
    for cid in sorted(known):
        if cid.lower() == lowered:
            return cid
    return None
