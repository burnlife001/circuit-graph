# -*- coding: utf-8 -*-
"""
pdf_table_reader.py — 从任意 PDF 中识别并提取所有表格(优化芯片 datasheet),
跨页表格按同名表头自动合并,输出单一 markdown 文件。

用法:
  python pdf_table_reader.py [pdf_path]
        [--pages PAGES] [--out OUT.md] [--flavor {lattice,stream,auto}]

默认:
  pdf_path  = 当前目录下第一个 *.pdf
  pages     = 全部页
  flavor    = auto(先 lattice,空则降级 stream)
  out       = tables-<pdf-stem>.md,与 PDF 同目录

依赖(camelot-py 2.0+):
  - pip install "camelot-py[base]"  (2.0+ 用 playa-pdf 后端,无需 Java/gs)
  - 若使用旧版 camelot-py 0.x,需要 Java JRE + Ghostscript
  - 推荐用 ~/__work/BaseTools/camelot/.venv 里的解释器
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 依赖检查
# ---------------------------------------------------------------------------
def check_deps():
    """依赖检查:只验 python 包 camelot;camelot-py 2.0+ 用 playa 后端,无需 Java/gs。

    旧版 camelot-py 0.x 仍需 Java JRE + Ghostscript,这里只在检测到旧版时提示。
    """
    errors = []
    camelot_ver = None
    try:
        import camelot  # noqa: F401
        camelot_ver = getattr(camelot, "__version__", "0.x?")
    except ImportError:
        errors.append(
            "✗ 缺 python 包: camelot-py\n"
            "    安装: pip install \"camelot-py[base]\""
        )
        camelot_ver = None

    if camelot_ver is not None:
        # 解析主版本号
        m = re.match(r"(\d+)\.", camelot_ver)
        major = int(m.group(1)) if m else 0
        if major < 2:
            # 旧版需要 Java+gs
            try:
                subprocess.run(
                    ["java", "-version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            except (FileNotFoundError, subprocess.CalledProcessError):
                errors.append(
                    "✗ 检测到 camelot-py 旧版 (<2.0),缺 Java JRE\n"
                    "    安装: OpenJDK 17+ 并把 bin\\ 加入 PATH\n"
                    "    或升级: pip install -U \"camelot-py[base]\""
                )
            gs_candidates = ["gs", "gswin64c", "gswin32c"]
            if not any(_which(exe) for exe in gs_candidates):
                errors.append(
                    "✗ 检测到 camelot-py 旧版 (<2.0),缺 Ghostscript\n"
                    "    安装: https://www.ghostscript.com/releases.html"
                )

    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        errors.append(
            "✗ 缺 pdfplumber(用于页面文本启发式)\n"
            "    安装: pip install pdfplumber"
        )

    try:
        from pypdf import PdfReader  # noqa: F401
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore  # noqa: F401
        except ImportError:
            errors.append(
                "✗ 缺 pypdf(用于拿 PDF 页数)\n"
                "    安装: pip install pypdf"
            )

    if errors:
        sys.stderr.write("=" * 60 + "\n")
        sys.stderr.write("pdf_table_reader 启动失败,缺以下依赖:\n\n")
        for e in errors:
            sys.stderr.write(e + "\n\n")
        sys.stderr.write("=" * 60 + "\n")
        sys.exit(1)


def _which(exe):
    """简易 which:检查 PATH 里是否有此 exe(Windows 还试 .exe/.bat/.cmd 后缀)。"""
    from shutil import which
    suffixes = [""] if os.name != "nt" else ["", ".exe", ".bat", ".cmd"]
    for s in suffixes:
        if which(exe + s):
            return True
    return False


# ---------------------------------------------------------------------------
# 页面参数解析(支持 "1-20", "1,2,3", "1-5,7,9-12")
# ---------------------------------------------------------------------------
def parse_pages(s, max_pages):
    """把 "1-20,3,5-7" 解析成 camelot 可用的 "1-20,3,5-7" 字符串(归一化 1-indexed)。"""
    if not s:
        return None  # camelot 默认全部
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            if a > b:
                a, b = b, a
            a = max(1, a)
            b = min(max_pages, b)
            out.append(f"{a}-{b}")
        else:
            n = int(part)
            n = max(1, min(max_pages, n))
            out.append(str(n))
    return ",".join(out) if out else None


def get_page_count(pdf_path):
    """用 PyPDF2 或 pdfplumber 拿页数(都不在依赖里则临时用 PyMuPDF)。"""
    # 优先用 camelot 自己的方式
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            sys.stderr.write(
                "✗ 需要 pypdf 或 PyPDF2 拿页数\n"
                "    安装: pip install pypdf\n"
            )
            sys.exit(1)
    return len(PdfReader(str(pdf_path)).pages)


# ---------------------------------------------------------------------------
# 表格归一化(用于跨页比较)
# ---------------------------------------------------------------------------
def _norm_cell(s):
    return re.sub(r"\s+", "", (s or "").strip()).lower()


def header_signature(df):
    """表格首行归一化,跨页比较用。"""
    if df is None or len(df) == 0:
        return ""
    return tuple(_norm_cell(c) for c in df.iloc[0].tolist())


def merge_cross_page(tables):
    """tables: list of (page_num:int, df:DataFrame, accuracy:float)
    返回 list of dict: {pages, df, accuracy, merged}
    """
    if not tables:
        return []
    out = []
    cur = {
        "pages": [tables[0][0]],
        "df": tables[0][1].copy(),
        "accuracy": tables[0][2],
    }
    cur_sig = header_signature(tables[0][1])
    for page, df, acc in tables[1:]:
        sig = header_signature(df)
        if sig and sig == cur_sig and len(df) > 1:
            # 同表头,合并(去掉第二页起的表头)
            cur["df"] = pd.concat([cur["df"], df.iloc[1:]], ignore_index=True)
            cur["pages"].append(page)
            cur["accuracy"] = min(cur["accuracy"], acc)
        else:
            out.append(cur)
            cur = {
                "pages": [page],
                "df": df.copy(),
                "accuracy": acc,
            }
            cur_sig = sig
    out.append(cur)
    for o in out:
        o["merged"] = len(o["pages"]) > 1
    return out


# ---------------------------------------------------------------------------
# 表格标题启发式
# ---------------------------------------------------------------------------
_TITLE_HINTS = re.compile(
    r"(table|pin|electrical|characteristics|register|truth|af|alternate|"
    r"multiplex|parameter|absolute|maximum|rating|ordering|information|"
    r"封装|引脚|电气|参数|特性|复用|寄存器|真值|最大|额定|订货)",
    re.I,
)


def guess_title(page_text, page_num, table_idx):
    """page_text: 一页所有非表格文本拼接,挑选最像表名的一行。"""
    if not page_text:
        return f"未命名(第 {page_num} 页)"
    # 取候选行(<=60 字符、非空、含至少一个字母)
    cands = []
    for line in page_text.splitlines():
        line = line.strip()
        if not line or len(line) > 60 or len(line) < 3:
            continue
        if not re.search(r"[A-Za-z一-鿿]", line):
            continue
        score = 0
        if _TITLE_HINTS.search(line):
            score += 5
        if "Table" in line or "表" in line:
            score += 3
        if re.search(r"\d+\s*[\.\-]", line):  # 编号标题 "1. xxx"
            score += 2
        if line.isupper():
            score += 1
        if score > 0:
            cands.append((score, line))
    if cands:
        cands.sort(key=lambda x: -x[0])
        return cands[0][1]
    # 没命中就给个中性标题
    return f"未命名(第 {page_num} 页 第 {table_idx + 1} 表)"


# ---------------------------------------------------------------------------
# 页面文本提取(用于标题启发式)
# ---------------------------------------------------------------------------
def extract_page_texts(pdf_path, pages_str):
    """用 pdfplumber 拿每页文本(纯文本,不解析表格)。返回 dict[page_num]->text"""
    try:
        import pdfplumber
    except ImportError:
        sys.stderr.write(
            "✗ 缺 pdfplumber(用于页面文本启发式 + camelot 失败时的备用提取)\n"
            "    安装: pip install pdfplumber\n"
        )
        sys.exit(1)
    out = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        # pages_str 是 camelot 风格的 "1,3-5",要还原成 page index 列表
        if pages_str:
            idxs = []
            for part in pages_str.split(","):
                if "-" in part:
                    a, b = part.split("-", 1)
                    idxs.extend(range(int(a) - 1, int(b)))
                else:
                    idxs.append(int(part) - 1)
        else:
            idxs = range(len(pdf.pages))
        for i in idxs:
            if 0 <= i < len(pdf.pages):
                out[i + 1] = pdf.pages[i].extract_text() or ""
    return out


# ---------------------------------------------------------------------------
# markdown 输出
# ---------------------------------------------------------------------------
def df_to_md_table(df):
    """DataFrame -> markdown 表格字符串。处理换行、转义竖线。"""
    rows = []
    for _, row in df.iterrows():
        cells = []
        for v in row.tolist():
            s = "" if v is None else str(v)
            s = s.replace("\r\n", "\n").replace("\r", "\n")
            s = s.replace("|", "\\|")  # 转义 markdown 表格分隔符
            s = s.replace("\n", "<br>")
            cells.append(s.strip())
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    # 表头就是第一行,加分隔行
    header = rows[0]
    ncol = df.shape[1]
    sep = "| " + " | ".join(["---"] * ncol) + " |"
    return "\n".join([header, sep] + rows[1:])


def write_markdown(out_path, pdf_path, merged_tables, page_texts, flavor_used, page_count):
    """写最终 markdown。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n_total = len(merged_tables)
    n_merged = sum(1 for t in merged_tables if t["merged"])

    lines = []
    lines.append(f"# {pdf_path.name} 表格提取")
    lines.append("")
    lines.append(
        f"> 提取时间: {now} | 总表格数: {n_total} | "
        f"跨页合并: {n_merged} 张 | 引擎: camelot/{flavor_used} | 总页数: {page_count}"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    table_seq = 0
    for t in merged_tables:
        table_seq += 1
        pages = t["pages"]
        page_text = "\n".join(page_texts.get(p, "") for p in pages)
        title = guess_title(page_text, pages[0], 0)

        if t["merged"]:
            page_label = f"第 {pages[0]}-{pages[-1]} 页"
            merge_note = f" (跨页合并: {len(pages)} 页)"
        else:
            page_label = f"第 {pages[0]} 页"
            merge_note = ""

        warn = ""
        if t["accuracy"] is not None and t["accuracy"] < 80:
            warn = " ⚠ 置信度低"

        lines.append(f"## Table {table_seq} · {page_label} · {title}{merge_note}{warn}")
        lines.append("")
        lines.append(df_to_md_table(t["df"]))
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out_path, n_total, n_merged


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def extract_tables(pdf_path, pages_str, flavor):
    """调用 camelot 提取。

    flavor='auto' 时:先 lattice,若 0 表则降级 stream。
    其他:按指定 flavor 跑。
    """
    import camelot
    global pd
    import pandas as pd

    if flavor == "auto":
        # 先 lattice
        tables_lat = camelot.read_pdf(
            str(pdf_path), pages=pages_str or "1", flavor="lattice"
        )
        if len(tables_lat) > 0:
            return tables_lat, "lattice"
        # lattice 0 表 -> 降级 stream
        sys.stderr.write(
            "ℹ lattice 未识别到表格,降级为 stream 模式...\n"
        )
        tables = camelot.read_pdf(
            str(pdf_path), pages=pages_str or "1", flavor="stream"
        )
        return tables, "stream(降级)"

    # 显式指定 flavor
    try:
        tables = camelot.read_pdf(
            str(pdf_path), pages=pages_str or "1", flavor=flavor
        )
    except Exception as e:
        sys.stderr.write(
            f"✗ camelot {flavor} 提取失败: {type(e).__name__}: {e}\n"
        )
        raise
    return tables, flavor


def main():
    ap = argparse.ArgumentParser(
        description="提取 PDF 中所有表格 → 单个 markdown 文件",
    )
    ap.add_argument("pdf", nargs="?", help="PDF 路径(默认当前目录第一个 *.pdf)")
    ap.add_argument(
        "--pages", help='页码范围,如 "1-20" 或 "1,3,5-7"(默认全部)'
    )
    ap.add_argument(
        "--out", help="输出 markdown 路径(默认 tables-<pdf-stem>.md)"
    )
    ap.add_argument(
        "--flavor", default="auto", choices=["lattice", "stream", "auto"],
        help="camelot 提取模式(默认 auto: 先 lattice,0 表则降级 stream)",
    )
    args = ap.parse_args()

    check_deps()

    # 找 PDF
    pdf_path = Path(args.pdf) if args.pdf else None
    if pdf_path is None:
        cands = sorted(Path(".").glob("*.pdf"))
        if not cands:
            sys.stderr.write("✗ 当前目录没有 .pdf 文件,也未指定路径参数\n")
            sys.exit(1)
        pdf_path = cands[0]
        print(f"ℹ 未指定 PDF,自动选用: {pdf_path}")
    if not pdf_path.is_file():
        sys.stderr.write(f"✗ PDF 不存在: {pdf_path}\n")
        sys.exit(1)

    # 拿页数 + 解析 --pages
    page_count = get_page_count(pdf_path)
    pages_str = parse_pages(args.pages, page_count) if args.pages else None
    if pages_str is None:
        page_count_eff = page_count
    else:
        # 用于 "1-end" 不支持时的回退
        # 计算实际选了多少页(仅用于显示)
        cnt = 0
        for part in pages_str.split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                cnt += int(b) - int(a) + 1
            else:
                cnt += 1
        page_count_eff = cnt

    # 提取
    print(f"ℹ 提取 {pdf_path.name} (页 {pages_str or 'all'}, flavor={args.flavor})...")
    tables, flavor_used = extract_tables(pdf_path, pages_str, args.flavor)
    print(f"ℹ camelot 检测到 {len(tables)} 张表")

    if len(tables) == 0:
        sys.stderr.write(
            "✗ camelot 没识别到任何表格。\n"
            "    可能原因:\n"
            "      - 表格是无线条的纯文本流(可手动加 --flavor stream 试试)\n"
            "      - PDF 是扫描件(需先 OCR)\n"
            "      - 页码超出范围(--pages 检查)\n"
        )
        sys.exit(2)

    # 整理成可比较的形式
    raw = [(int(t.page), t.df, float(t.accuracy) if t.accuracy else 0.0)
           for t in tables]
    # 按 (page, order) 排序(camelot 已按页内顺序给 order)
    raw.sort(key=lambda x: (x[0],))

    # 跨页合并
    merged = merge_cross_page(raw)

    # 拿页面文本(用于标题启发式)
    page_texts = extract_page_texts(pdf_path, pages_str)

    # 输出
    out_path = (
        Path(args.out) if args.out
        else pdf_path.with_name(f"tables-{pdf_path.stem}.md")
    )
    out_path, n_total, n_merged = write_markdown(
        out_path, pdf_path, merged, page_texts, flavor_used, page_count_eff
    )

    print(f"✓ 已写入: {out_path}")
    print(f"  总表数: {n_total}  跨页合并: {n_merged}  引擎: {flavor_used}")


if __name__ == "__main__":
    main()
