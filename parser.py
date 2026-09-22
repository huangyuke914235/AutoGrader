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
            prev.char_end = prev.char_end + len(s.text) + 1
        else:
            merged.append(s)
    return merged


def _fallback_chunks(full_text: str) -> list:
    secs = []
    for i in range(0, len(full_text), CHUNK_SIZE):
        chunk = full_text[i:i + CHUNK_SIZE]
        secs.append(Section(id=f"s{len(secs)+1}", title=f"第{len(secs)+1}段",
                            level=1, text=chunk, char_start=i, char_end=i + len(chunk)))
    return secs


def extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import fitz
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
        import docx
        d = docx.Document(path)
        blocks = [p.text for p in d.paragraphs]
        for tb in d.tables:
            for row in tb.rows:
                blocks.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(blocks)
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise ValueError(f"暂不支持的文件类型：{ext}（只支持 pdf / docx / txt / md）")


def canonical(text: str) -> str:
    """把正文规范成「所有连续空白都压成一个空格」的单一形态。

    为什么要做：PDF 抽取出来的正文几乎每行都被斩断（实测 S08/S09/S10 平均每 19 字
    一个换行），模型引用的是通顺的句子，拿去和带换行的原文做逐字匹配必然失败，
    于是整条判定被作废、评分点被判成 0 分。

    这不是放宽匹配规则：正文与引用**两侧用同一套规则**规范化，
    匹配仍然是精确匹配，只是把「抽取产生的换行」这个 artifact 消掉了。
    """
    return re.sub(r"\s+", " ", text).strip()


def parse_file(path: str):
    """返回 (canonical_text, sections)

    注意：章节切分仍然在**原始文本**上做（标题识别依赖换行），
    切完再把全文与每个章节各自规范化，并按规范后的文本重算偏移量，
    否则详情页的「定位 / 高亮」会错位。
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
    return full_text, sections


FULL_TEXT_LIMIT = 40000   # 约 27k tokens；正确性优先，超长文档才走召回


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
