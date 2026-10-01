# -*- coding: utf-8 -*-
"""多模态 OCR —— 让「读不到」的内容进入证据链

设计目标（这是本模块存在的唯一理由）：
实验报告里最关键的证据往往是**截图**：运行结果、报错信息、IDE 界面、结果表格、
UML 图。纯文本抽取拿到的是「运行结果示例」这行标题，正文全是空的。旧版本因此
把一整类报告系统性判低（`README` 里自己承认的「不支持图片与截图」）。

本模块把每一页渲染成图片，交给**多模态模型**（DeepSeek-V4.1-Flash，`deepseek-flash`，
官方定价页标注 Vision ✓）读出来，产出一份**可引用、可审计、可缓存**的补全文本。

四条纪律（与项目既有的三条铁律同源）：

1. **只读不猜**。提示词明确禁止推断与美化：看不清就写「无法辨认」。
   模型在 OCR 阶段**没有评分权**，它只负责转录。
2. **页码即坐标**。每个页面的产出都带上「第 N 页」，最终拼成带标题的章节，
   于是引用可以精确到页 —— 证据锚定（A 阶段）仍然由代码做逐字校验。
3. **不使用即不花钱**。结果按「文件内容哈希 + 渲染参数 + 提示词版本」缓存到
   `.ocr_cache/`，同一份报告重复评阅只调一次模型。缓存目录已 gitignore。
4. **OCR 失败不能拖垮评阅**。任何异常都降级为「本页未读出」，并在报告里
   如实写明，绝不静默变出一段不存在的文本。

为什么**逐页**调用而不是整份塞进去：一页一张图，注意力不被稀释，页码归属不会串，
失败也只损失一页；代价是调用次数等于页数，所以必须配上缓存与页数上限。
"""
import base64
import hashlib
import io
import json
import os
import re

import parser as P

# ---------- 常量 ----------

#: 走 OCR 的默认 DPI。150 是「小字代码也能认」和「base64 体积可接受」的平衡点：
#: 72 太小（代码糊成一团），300 太大（一页 PNG 可到 1.5MB，base64 后翻 1.33 倍）。
DEFAULT_DPI = 150

#: 一次评阅最多读多少页。防止有人上传 300 页的扫描书把额度跑光。
#: 超出部分不静默丢弃 —— 会在结果里写明「只读了前 N 页」。
DEFAULT_MAX_PAGES = 20

#: 单页 PNG 的字节上限。超了就降 DPI 重渲染，而不是把超大 base64 发出去。
MAX_IMAGE_BYTES = 1_600_000

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ocr_cache")

#: 提示词版本。改了下面的提示词必须把这个数字加一，否则旧缓存会被复用，
#: 出现「代码是新的、产出是旧的」这种最难查的不一致。
PROMPT_VERSION = "ocr-v1"

SYSTEM_PROMPT = (
    "你是一个严谨的文档转录工具，不是助手，不是评分员。"
    "你唯一的任务是把图片里**看得见**的内容逐字转录出来。"
    "绝对禁止推断、补全、美化、总结或评价。"
)

USER_PROMPT = """请转录这张实验报告页面（第 {page} 页，共 {total} 页）里可见的全部内容，输出 JSON。

要求：
1. 「text」：逐字转录页面上的所有文字，包括标题、正文、代码、命令行输出、报错信息、
   表格内容。保留原有的换行与缩进层级；代码不要加注释、不要改格式。
2. 「images」：对每一处**非文字**内容单独写一条，说明它在图里显示了什么。
   这是给评阅老师看的旁证，不是让你发挥：
   - kind：screenshot（运行结果/界面截图）/ code（代码截图）/ table（表格截图）/
     chart（图表）/ diagram（流程图、UML、结构图）/ formula（公式）/ other
   - caption：这张图/表在显示什么（一句话）
   - content：图里可读出的**具体内容**，例如控制台实际输出的数值、表格的列名与数据、
     图表的坐标轴与趋势结论。读不出来就写「无法辨认」，不要猜。
3. 「legible」：这张图是否清晰可读。true / false。
4. 「notes」：只用于说明影响转录质量的问题（模糊、遮挡、纯装饰图）。
   没有问题就留空字符串。

再强调一次：看不清就写「无法辨认」。「看不出内容」和「没有内容」是两件不同的事，
后者会让一份写得好的报告被判低分。"""

SCHEMA_HINT = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "caption": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["kind", "caption"],
            },
        },
        "legible": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["text"],
}

IMAGE_KINDS = ("screenshot", "code", "table", "chart", "diagram", "formula", "other")

_KIND_CN = {
    "screenshot": "截图",
    "code": "代码截图",
    "table": "表格",
    "chart": "图表",
    "diagram": "结构图",
    "formula": "公式",
    "other": "图片",
}


# ---------- 是否需要 OCR ----------

CAPTION_PROMPT = """请描述这张实验报告页面（第 {page} 页，共 {total} 页）里**图片/图表/截图**的内容，输出 JSON。

这一页的正文文字已经能从文档里抽取出来，你**不需要**转录正文，只需处理图片信息。
也不要复述页面上已经有的文字段落。

要求：
1. 「text」：固定输出空字符串。正文由文档解析层提供，你重复一遍只会造成重复计分。
2. 「images」：对每一处非文字内容单独写一条：
   - kind：screenshot（运行结果/界面截图）/ code（代码截图）/ table（表格截图）/
     chart（图表）/ diagram（流程图、UML、结构图）/ formula（公式）/ other
   - caption：这张图/表在显示什么（一句话）
   - content：图里可读出的**具体内容**，尤其是控制台实际输出的数值、
     表格的列名与数据、图表反映的趋势。读不出来就写「无法辨认」，不要猜。
3. 「legible」：这张图是否清晰可读。
4. 「notes」：只写影响阅读的问题（模糊、遮挡）；没有问题留空。

强调：看不清就写「无法辨认」。「看不出内容」和「没有内容」是两件不同的事。"""


def _looks_like_code_or_output(block: str) -> bool:
    """判断一段文字里是否有「代码/命令行输出」特征。

    为什么要看这个：正文里出现 class/public static/报错栈，说明这份报告**把代码
    以文本形式写进去了**，那图片里大概率只是重复截图，OCR 的边际收益低。
    """
    if not block:
        return False
    markers = re.compile(
        r"(public\s+(static\s+)?(void|class|int|String)|class\s+\w+\s*\{|"
        r"def\s+\w+\s*\(|import\s+\w+|System\.out|console\.log|printf|"
        r"Traceback\s+\(most\s+recent|Exception\s+in\s+thread|Error:|"
        r"^\s*\$|^\s*>>>|^\s*PS\s+[A-Z]:)")
    return bool(markers.search(block))


def vision_needed(full_text: str, warnings=None, embedded_images: int = 0) -> dict:
    """判断一份报告是否值得走多模态 OCR，并给出人话理由与**该用哪种档位**。

    返回 {"needed": bool, "mode": "transcribe"|"caption"|"", "reasons": [...], "signals": {...}}

    两档位不是省事，是两个不同的目的：
    - "transcribe"（转录）：正文里没有代码与输出，证据基本只存在于图片里。
      这时必须把图片文字**读成可引用的正文**，否则整类报告被系统性判低。
    - "caption"（只描述图）：正文里已经有代码与输出，图片多半是重复截图。
      这时读全文收益低，但只要把「第 N 页有一张控制台截图，显示 …」写进正文，
      「结果与数据」这类评分点就有据可依，成本远低于全量转录。

    判据（按证据强度排序）：
    1. 解析层已告警（疑似扫描件 / 正文极短）—— 最强信号
    2. 提到截图/运行结果，但正文里找不到代码或输出 —— 典型的「证据全在图片里」
    3. 存在嵌入图片；正文里有代码时降为 caption 档
    4. 可抽取正文过短，且没有内联代码 —— 几乎可以断定是图片型报告
    """
    reasons = []
    text = full_text or ""
    n_chars = len(text.strip())
    has_shot_word = bool(re.search(r"(截图|运行结果|结果示例|界面|控制台|报错|错误信息)", text))
    has_code = _looks_like_code_or_output(text)

    # 1. 解析层告警
    scan_like = [w for w in (warnings or []) if "扫描" in w or "空" in w or "过短" in w]
    if scan_like:
        reasons.append("解析阶段已告警：" + "；".join(scan_like))

    # 2. 「标题在、内容不在」——最典型的图片型报告
    if has_shot_word and not has_code:
        reasons.append("报告多处提到截图/运行结果，但正文里找不到代码或命令行输出"
                       "——证据很可能全在图片里")

    # 3. 嵌入图片：按正文是否已有代码决定档位
    if embedded_images > 0:
        if has_code:
            reasons.append(f"检测到 {embedded_images} 处嵌入图片；正文已有代码与输出，"
                           f"按「只描述图片」档读取即可")
        else:
            reasons.append(f"检测到 {embedded_images} 处嵌入图片")

    # 4. 文本层过薄：只有在**没有**内联代码时才足以判定为图片型报告
    if n_chars < 300 and not has_code:
        reasons.append(f"可抽取正文仅 {n_chars} 字且没有内联代码，"
                       f"属于「几乎没有文本层」的报告")

    if not reasons:
        return {"needed": False, "mode": "", "reasons": [],
                "signals": {"text_chars": n_chars, "has_screenshot_heading": has_shot_word,
                            "has_inline_code": has_code, "embedded_images": embedded_images}}

    # 正文里已有代码/输出 → 图片只是旁证，用 caption 档；否则必须转录
    mode = "caption" if (has_code and not scan_like) else "transcribe"
    return {
        "needed": True,
        "mode": mode,
        "reasons": reasons,
        "signals": {
            "text_chars": n_chars,
            "has_screenshot_heading": has_shot_word,
            "has_inline_code": has_code,
            "embedded_images": embedded_images,
        },
    }


# ---------- 缓存 ----------

def _file_digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


def cache_path(digest: str, page_no: int, dpi: int, model: str,
               transcribe: bool = True) -> str:
    # 档位（转录 / 只描述图）必须进缓存键：同一页在两种档位下产出完全不同，
    # 混用会让「只描述了图」的结果被当成「整页转录」用。
    key = (f"{digest}_p{page_no}_d{dpi}_{model}_{PROMPT_VERSION}"
           f"_{'tr' if transcribe else 'cap'}")
    return os.path.join(CACHE_DIR, hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def cache_get(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None       # 缓存坏了就当没有，不能因为缓存把整条链路带崩


def cache_put(path: str, data: dict):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass              # 写不进去只损失一次缓存，不影响本次结果


# ---------- 渲染 ----------

def render_for_ocr(path: str, dpi: int = DEFAULT_DPI, max_pages: int = DEFAULT_MAX_PAGES):
    """把文件渲染成 [(页码, PNG 字节), ...]。

    只支持 PDF 与图片文件本身；DOCX 的图片由 parser 单独抽出（不走这里）。
    PNG 过大时自动降 DPI 重渲染一次 —— 宁可略糊，也不要因为发不出去而整页读不到。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        with open(path, "rb") as f:
            return [(1, f.read())]

    if ext != ".pdf":
        return []

    pages = P.render_pdf_pages(path, max_pages=max_pages, dpi=dpi)
    out = []
    for i, png in enumerate(pages, 1):
        if len(png) > MAX_IMAGE_BYTES and dpi > 96:
            smaller = P.render_pdf_pages(path, max_pages=i, dpi=96)
            if smaller:
                png = smaller[-1]
        out.append((i, png))
    return out


# ---------- 模型调用 ----------

def _b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def read_one_page(client, model: str, png: bytes, page_no: int, total: int,
                  timeout: int = 180, transcribe: bool = True) -> dict:
    """用多模态模型读一页。返回 {text, images, legible, notes, error}。

    走的是与 `llm.call_json` 同一个 OpenAI 兼容客户端，但**不能复用 call_json**：
    call_json 只接受纯文本的 user 消息，而多模态要求 content 是内容块数组。

    transcribe=False 走「只描述图片」档：正文由文档解析层提供，让模型只写图片信息，
    既省输出 token，也避免它把正文复述一遍造成重复内容参与判定。
    """
    prompt = USER_PROMPT if transcribe else CAPTION_PROMPT
    user_content = [
        {"type": "text", "text": prompt.format(page=page_no, total=total)},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{_b64(png)}"}},
    ]

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user_content}],
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    raw = resp.choices[0].message.content or ""

    from llm import loads_json
    data = loads_json(raw)

    text = str(data.get("text") or "").strip()
    images = []
    for it in (data.get("images") or []):
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "other").strip().lower()
        if kind not in IMAGE_KINDS:
            kind = "other"
        images.append({
            "kind": kind,
            "caption": str(it.get("caption") or "").strip(),
            "content": str(it.get("content") or "").strip(),
        })
    return {
        "text": text,
        "images": images,
        "legible": bool(data.get("legible", True)),
        "notes": str(data.get("notes") or "").strip(),
        "error": "",
    }


# ---------- 主入口 ----------

def read_report_images(path: str, api_key: str = None, base_url: str = None,
                       model: str = None, dpi: int = DEFAULT_DPI,
                       max_pages: int = DEFAULT_MAX_PAGES, progress=None,
                       use_cache: bool = True, transcribe: bool = True) -> dict:
    """读一份报告的所有页面。返回：

        {
          "pages": [ {page, text, images, legible, notes, chars, cached} ],
          "stats": {pages_read, cached_hits, chars, images, failed, model, dpi, elapsed?},
          "errors": [ "第 3 页：..." ],
        }

    失败是**逐页**隔离的：第 3 页读不出来，第 4 页照常读，最后在 errors 里列清楚。
    """
    import time
    from llm import get_env, _get_client

    t0 = time.time()
    model = model or get_env("LLM_MODEL", "deepseek-flash")
    rendered = render_for_ocr(path, dpi=dpi, max_pages=max_pages)

    pages, errors = [], []
    cached_hits = 0
    if not rendered:
        return {"pages": [], "stats": {"pages_read": 0, "cached_hits": 0, "chars": 0,
                                       "images": 0, "failed": 0, "model": model,
                                       "dpi": dpi, "elapsed": 0.0},
                "errors": ["该文件格式不支持多模态读取（只支持 PDF 与图片）"]}

    digest = _file_digest(path)
    total = len(rendered)
    client = None

    for page_no, png in rendered:
        if progress:
            progress(page_no, total)
        cp = cache_path(digest, page_no, dpi, model, transcribe)
        got = cache_get(cp) if use_cache else None
        if got is not None:
            got = dict(got)
            got["page"] = page_no
            got["cached"] = True
            pages.append(got)
            cached_hits += 1
            continue

        try:
            if client is None:
                client = _get_client(240, api_key=api_key, base_url=base_url)
            res = read_one_page(client, model, png, page_no, total, transcribe=transcribe)
        except Exception as e:
            # 逐页隔离：一页失败不拖垮整份报告
            errors.append(f"第 {page_no} 页：{type(e).__name__}: {str(e)[:160]}")
            res = {"text": "", "images": [], "legible": False,
                   "notes": "", "error": f"{type(e).__name__}"}

        res["page"] = page_no
        res["cached"] = False
        res["chars"] = len(res.get("text") or "")
        pages.append(res)
        if not res.get("error"):
            cache_put(cp, {k: v for k, v in res.items() if k not in ("page", "cached")})

    stats = {
        "pages_read": len(pages),
        "cached_hits": cached_hits,
        "chars": sum(p.get("chars", len(p.get("text") or "")) for p in pages),
        "images": sum(len(p.get("images") or []) for p in pages),
        "failed": len(errors),
        "model": model,
        "dpi": dpi,
        "mode": "transcribe" if transcribe else "caption",
        "elapsed": round(time.time() - t0, 1),
    }
    return {"pages": pages, "stats": stats, "errors": errors}


# ---------- 落进正文 ----------

def build_section_text(pages) -> str:
    """把 OCR 结果拼成可被 `parser.split_sections` 切分的带标题正文。

    标题格式固定为「第 N 页 OCR 转录」，于是：
    - 会被切成独立章节，有 section_id，能被 `llm.locate` 定位；
    - 证据引用可以精确到页，老师一眼能翻到那张截图；
    - `[截图]` / `[图表]` 前缀把「转录文字」与「图片说明」区分开，
      模型在判定时不会把图片说明误当成学生写的正文。
    """
    blocks = []
    for p in pages or []:
        # 标题后必须留一个空行：章节切分器把「下一行为空」当作标题的判据之一
        # （见 parser._is_heading）。不留空行的话，标题会被并进正文，
        # 证据就失去了「第 N 页」这个坐标 —— 那样 OCR 的意义就丢了一半。
        lines = [f"第 {p.get('page', '?')} 页 OCR 转录", ""]
        text = (p.get("text") or "").strip()
        if text:
            lines.append(text)
        elif not p.get("images"):
            lines.append("（本页无可转录文字）")

        for img in (p.get("images") or []):
            kind = _KIND_CN.get(img.get("kind", "other"), "图片")
            caption = (img.get("caption") or "").strip()
            content = (img.get("content") or "").strip()
            seg = f"[{kind}] {caption}".rstrip()
            if content:
                seg += f"：{content}"
            lines.append(seg)

        note = (p.get("notes") or "").strip()
        if note:
            lines.append(f"（转录说明：{note}）")
        blocks.append("\n".join(lines))

    if not blocks:
        return ""
    head = ("多模态 OCR 转录（由模型从报告页面图片中读出，仅供核对；"
            "图片中无法辨认的内容不会作为评分依据）")
    return head + "\n" + "\n\n".join(blocks) + "\n"


def enrich_text(full_text: str, ocr_pages, title: str = "多模态 OCR 转录"):
    """原文 + OCR 补全文本。原文在前，保证既有引用的定位不被打乱。"""
    extra = build_section_text(ocr_pages)
    if not extra:
        return full_text or ""
    return (full_text or "").rstrip() + "\n\n" + extra
