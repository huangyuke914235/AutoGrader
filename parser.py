# -*- coding: utf-8 -*-
"""报告解析（S0 · 不调用任何模型，纯确定性代码）

目标：PDF / DOCX / TXT -> 全文 + 带偏移的章节列表
偏移量很重要：它是前端高亮定位和 evidence 校验的基础。
"""
import os
import re

from models import Section

# 中文实验报告常见标题写法
HEADING_PATTERNS = [
    re.compile(r"^\s*第\s*[一二三四五六七八九十]+\s*[章节部分]\s*.*$"),
    re.compile(r"^\s*[一二三四五六七八九十]+\s*[、.．]\s*\S.{0,30}$"),
    re.compile(r"^\s*\d+(\.\d+){0,2}\s*[、.．]?\s*\S.{0,40}$"),
    re.compile(r"^\s*(实验目的|实验内容|实验要求|实验环境|实验原理|实验步骤|实验过程|"
               r"实验设计与实现|实验结果|结果分析|实验分析|结果与分析|实验总结|"
               r"心得体会|思考题|源代码|核心代码|附录|参考文献|问题描述|算法设计|"
               r"测试与结果|总结与展望|结论|引言|相关工作|方法|摘 要|摘要)\s*[:：]?\s*$"),
]

MIN_SECTION_CHARS = 40     # 太短的章节并入上一个
CHUNK_SIZE = 800           # 切分失败时的降级定长块


def _is_heading(line: str, next_line: str = "") -> bool:
    """判断一行是不是标题。

    中文实验报告的标题写法非常杂，纯正则枚举必然漏（实测 S02 全部漏掉）。
    所以用「结构启发式」兜底：短、无句末标点、且下一行更长 —— 大概率是标题。
    宁可切细不可漏切：切细了会被 merge 合并，漏切会让召回拿不到正文。
    """
    s = line.strip()
    if not s or len(s) > 30 or len(s) < 2:
        return False
    if s.endswith(("。", "；", "，", ",", ";", "、", "？", "?", "！", "!")):
        return False

    if any(p.match(line) for p in HEADING_PATTERNS):
        return True

    nxt = (next_line or "").strip()
    if s.endswith("："):
        return True
    if nxt == "":
        return True
    if len(nxt) > len(s) * 1.6:      # 短标题 + 长正文
        return True
    return False


def _level(title: str) -> int:
    t = title.strip()
    if re.match(r"^\s*\(?\d+(\.\d+){2}", t):
        return 3
    if re.match(r"^\s*\(?\d+(\.\d+)", t):
        return 2
    return 1


def split_sections(full_text: str) -> list:
    """按标题切分；切不出来就降级为定长块（保证流程不崩）"""
    sections = []
    lines = full_text.split("\n")

    heads = []          # (line_index, title)

    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _is_heading(ln, nxt):
            heads.append((i, ln.strip()))

    # 一个标题都没有 -> 降级
    if len(heads) < 2:
        return _fallback_chunks(full_text)

    # 第一个标题之前的正文也要保留（封面、摘要等）
    if heads and heads[0][0] > 0:
        pre = "\n".join(lines[:heads[0][0]]).strip()
        if len(pre) >= MIN_SECTION_CHARS:
            sections.append(Section(id="s1", title="报告头部", level=1,
                                    text=pre, char_start=0, char_end=len(pre)))

    for idx, title in heads:
        start = idx
        end = next((h[0] for h in heads if h[0] > idx), len(lines))
        body = "\n".join(lines[start:end]).strip()
        char_start = len("\n".join(lines[:start]))
        if char_start > 0:
            char_start += 1
        sections.append(Section(
            id=f"s{len(sections)+1}",
            title=title[:40],
            level=_level(title),
            text=body,
            char_start=char_start,
            char_end=char_start + len(body),
        ))

    # 合并过短的段
    merged = []
    for s in sections:
        if merged and len(s.text) < MIN_SECTION_CHARS:
            prev = merged[-1]
            prev.text = prev.text + "\n" + s.text
        else:
            merged.append(s)
    # 合并后必须按**真实位置**重算偏移：旧实现只是长度累加，
    # 会让 char_end 与真实引用位置对不上（P1-3）
    return recompute_offsets(merged, full_text)


def recompute_offsets(sections, full_text: str):
    """按各段文本在全文中的真实位置重算 char_start / char_end"""
    pos = 0
    for s in sections:
        if not s.text:
            s.char_start = pos
            s.char_end = pos
            continue
        i = full_text.find(s.text, pos)
        if i < 0:                 # 极端情况（文本被改写）退化为顺序推进，不做假偏移
            i = pos
        s.char_start = i
        s.char_end = i + len(s.text)
        pos = s.char_end
    return sections


def _fallback_chunks(full_text: str) -> list:
    secs = []
    for i in range(0, len(full_text), CHUNK_SIZE):
        chunk = full_text[i:i + CHUNK_SIZE]
        secs.append(Section(id=f"s{len(secs)+1}", title=f"第{len(secs)+1}段",
                            level=1, text=chunk, char_start=i, char_end=i + len(chunk)))
    return secs


def import_fitz():
    """PyMuPDF 的兼容导入：新版本包名叫 `pymupdf`，老版本叫 `fitz`。

    这个判断只允许有一处 —— parser 负责**读** PDF，report_pdf 负责**写** PDF，
    两边必须落到同一个库对象上，否则不同模块各自嘗試导入会拿到两套行为。
    """
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    return fitz


def extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        fitz = import_fitz()
        doc = fitz.open(path)
        parts = []
        for page in doc:
            blocks = page.get_text("blocks")
            blocks.sort(key=lambda b: (round(b[1], 1), b[0]))
            parts.append("\n".join(str(b[4]) for b in blocks))
        text = "\n".join(parts)
        doc.close()
        return text
    if ext == ".docx":
        # 按文档 XML 的真实顺序读段落与表格（旧实现把表格全追加到文末，破坏原文顺序）
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        d = docx.Document(path)
        blocks = []
        for child in d.element.body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                blocks.append(Paragraph(child, d).text)
            elif tag == "tbl":
                tb = Table(child, d)
                for row in tb.rows:
                    blocks.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(blocks)
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise ValueError(f"暂不支持的文件类型：{ext}（只支持 pdf / docx / txt / md）")


SHORT_TEXT_CHARS = 200        # 低于此字数基本可以判定为扫描件/图片型报告

PREVIEW_MAX_PAGES = 20        # 单份最多渲染多少页（防止超长 PDF 把内存吃满）
PREVIEW_DPI = 100             # 够看清版面与截图，又不至于让单页图片过大


def _open_pdf(path: str):
    """打开 PDF：优先新包名 pymupdf，兼容旧包名 fitz。
    文件不存在时**显式报错**（预览失败要能被发现，不静默返回空）。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到文件：{path}")
    return import_fitz().open(path)


def render_pdf_pages(path: str, max_pages: int = PREVIEW_MAX_PAGES,
                     dpi: int = PREVIEW_DPI):
    """把 PDF 每页渲染成 PNG 字节，供教师人工对照版面。

    边界说明（很重要，别越界）：
    - 本函数**不产生任何文本**，因此不参与判定、不进证据链、不影响任何指标；
      它解决的是"老师想核对某张截图/图表/公式，却要另外打开原文件"的不便。
    - 本函数**不做 OCR**：图片里的内容依然是"看得见、读不到"，
      相关判定仍按现状处理，界面会如实说明这一点。
    - 隐私：只在内存中返回字节，调用方用完即弃，**不落盘**。

    仅支持 PDF；其它格式返回空列表。
    """
    if os.path.splitext(path)[1].lower() != ".pdf" or max_pages <= 0:
        return []
    zoom = dpi / 72.0
    pages = []
    doc = _open_pdf(path)
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pages.append(page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png"))
    finally:
        doc.close()
    return pages


def preview_caption(n_rendered: int, total_pages: int) -> str:
    """预览区说明文案：把「能看」与「能读」的区别讲清楚"""
    head = f"已渲染 {n_rendered} 页原始版面"
    if total_pages > n_rendered:
        head += f"（共 {total_pages} 页，仅渲染前 {n_rendered} 页）"
    return (head + "，仅供人工对照。图片与截图中的内容不参与自动判定"
                   "（系统不做 OCR，读不到的内容不会成为评分依据）。")


def pdf_page_count(path: str) -> int:
    """PDF 总页数（用于预览文案；非 PDF 返回 0）"""
    if os.path.splitext(path)[1].lower() != ".pdf":
        return 0
    try:
        doc = _open_pdf(path)
        n = doc.page_count
        doc.close()
        return n
    except Exception:
        return 0          # 页数只用于文案，拿不到就不显示，不影响主流程


def inspect_text(full_text: str, n_sections: int = 0, pages: int = 0) -> dict:
    """解析体检：把「这份报告我们能读到什么、读不到什么」明确说出来。

    绝不能静默地把一份扫描件当成正常报告评出分数——那是最坏的一种错误。
    """
    warnings = []
    n = len(full_text or "")
    if n == 0:
        warnings.append("未提取到任何文本：无法评阅，请确认文件不是空文件")
    elif n < SHORT_TEXT_CHARS:
        warnings.append(f"正文仅 {n} 字，疑似扫描件/图片型报告；"
                        f"本项目不做 OCR，图片与截图中的内容不可见，判定可能严重偏低")
    if n > FULL_TEXT_LIMIT:
        warnings.append(f"正文 {n} 字超过 {FULL_TEXT_LIMIT} 字上限，判定将改用关键词召回模式，"
                        f"可能漏掉未被召回的内容，相关判定建议人工复核")
    if n_sections == 0 and n:
        warnings.append("未能切分出任何章节，判定将基于整篇文本，证据定位可能不准")
    return {
        "chars": n,
        "sections": n_sections,
        "pages": pages,
        "coverage": "full" if 0 < n <= FULL_TEXT_LIMIT else ("empty" if n == 0 else "retrieved"),
        "warnings": warnings,
    }


def canonical(text: str) -> str:
    """把正文规范成「所有连续空白都压成一个空格」的单一形态。

    为什么要做：PDF 抽取出来的正文几乎每行都被斩断（实测 S08/S09/S10 平均每 19 字
    一个换行），模型引用的是通顺的句子，拿去和带换行的原文做逐字匹配必然失败，
    于是整条判定被作废、评分点被判成 0 分。

    这不是放宽匹配规则：正文与引用**两侧用同一套规则**规范化，
    匹配仍然是精确匹配，只是把「抽取产生的换行」这个 artifact 消掉了。
    """
    return re.sub(r"\s+", " ", text).strip()


def parse_file(path: str, keep_lines: bool = False):
    """返回 (canonical_text, sections)；keep_lines=True 时多返回一份保留换行的原文。

    注意：章节切分仍然在**原始文本**上做（标题识别依赖换行），
    切完再把全文与每个章节各自规范化，并按规范后的文本重算偏移量，
    否则详情页的「定位 / 高亮」会错位。

    为什么要 keep_lines：canonical() 会把换行也压成空格，正文变成一行。
    这对「引用逐字匹配」是必需的，但对**行级规则**（按行扫描小标题、识别编号步骤、
    识别数据行）是致命的 —— 规则引擎拿不到行，就会把一份完整报告判成缺章节。
    两条链路各取所需：评阅/AI 用 canonical，行级规则用 raw。
    """
    raw = extract_text(path)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\u3000", " ", raw)

    sections = split_sections(raw)
    full_text = canonical(raw)

    pos = 0
    for s in sections:
        s.text = canonical(s.text)
        i = full_text.find(s.text, pos)
        s.char_start = i if i >= 0 else pos
        s.char_end = s.char_start + len(s.text)
        pos = max(s.char_end, s.char_start)
    if keep_lines:
        return full_text, sections, raw
    return full_text, sections


FULL_TEXT_LIMIT = 70000   # 约 45k tokens；正确性优先，只有更长的文档才走召回
# 为什么从 40000 提到 70000（2026-09-23 实测）：
#   S06（67779 字）原先走召回，而召回预算最多只覆盖全文 31%，
#   结果 r3/r4/r5 三项直接判 0（人工分别是 14/4/3 分），总分 27 vs 人工 43 —— **系统性假阴性**。
#   提高上限后，这类文档直接给全文，从根上避免"没看到就判没有"。
#   代价：单次调用上下文变大（token 成本与延迟上升），这是为正确性付的钱。
#   已发布的 9 份指标不受影响：它们都在 2.5 万字以内，本来就走的全文模式。


def build_context(sections, full_text: str, keywords=None, top_k: int = 6,
                  per_sec: int = 3500):
    """自适应上下文。

    小文档直接给全文 —— 保证判定正确性优先。
    只有超长文档才走召回，避免把报告切碎导致假阴性（这是实测踩过的坑）。
    """
    if len(full_text) <= FULL_TEXT_LIMIT:
        return full_text, True
    picked = retrieve(sections, keywords, top_k=top_k)
    ctx = "\n\n".join(f"[{s.id}] {s.title}\n{s.text[:per_sec]}" for s in picked)
    return ctx, False


def retrieve(sections: list, keywords, top_k: int = 4):
    """轻量召回：按关键词命中数给章节打分。

    不用向量库 —— 这是团队能力约束下的主动取舍，不是技术偷懒。
    """
    if not keywords:
        return sections[:top_k]
    scored = []
    for s in sections:
        hit = sum(1 for k in keywords if k and k in s.text)
        scored.append((hit, len(s.text), s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = [s for h, _, s in scored[:top_k] if h > 0]
    return picked or sections[:top_k]
