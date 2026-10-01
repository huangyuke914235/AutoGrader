# -*- coding: utf-8 -*-
"""多模态 OCR 的行为测试

覆盖的都是「出错了会很难查」的点：
- 页面渲染走的是被修过 bug 的 parser.render_pdf_pages（fitz 作用域问题）
- 缓存命中/失效的判据（换了提示词必须失效，否则会出现新旧不一致）
- 逐页失败隔离（一页挂掉不能拖垮整份报告）
- 拼进正文后的**标题可切分**（切不出章节，证据就无法定位到页）

全部使用 mock：不需要 API Key、不联网、不需要真实 PDF。
"""
import base64
import json
import os

import pytest

import ocr
import parser as P


# ---------- 测试替身 ----------

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None


class _Completions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0) if self.payloads else {"text": ""}
        if isinstance(payload, Exception):
            raise payload
        return _Resp(json.dumps(payload, ensure_ascii=False))


class _Client:
    def __init__(self, payloads):
        self.chat = type("C", (), {"completions": _Completions(payloads)})()


@pytest.fixture
def fake_pdf(tmp_path, monkeypatch):
    """一份假 PDF 文件 + 被替换掉的页面渲染器（不依赖 PyMuPDF）"""
    p = tmp_path / "report.pdf"
    p.write_bytes(b"%PDF-1.4 fake")

    def fake_render(path, max_pages=20, dpi=100):
        return [b"\x89PNG-page-%d" % i for i in range(1, 4)][:max_pages]

    monkeypatch.setattr(P, "render_pdf_pages", fake_render)
    return str(p)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "CACHE_DIR", str(tmp_path / "cache"))
    return str(tmp_path / "cache")


# ---------- vision_needed：该不该读图 ----------

def test_vision_needed_flags_screenshot_only_report():
    """标题写着「运行结果」，正文里却没有代码和输出 —— 典型证据在图片里"""
    text = ("实验报告\n实验步骤\n" + "步骤说明。" * 20 +
            "\n运行结果\n截图说明：需包含控制台输出截图\n")
    sig = ocr.vision_needed(text)
    assert sig["needed"] is True
    assert any("截图" in r for r in sig["reasons"])
    assert sig["signals"]["has_screenshot_heading"] is True
    assert sig["signals"]["has_inline_code"] is False


def test_vision_needed_skips_report_with_inline_code_and_output():
    """正文里已经有代码与输出：图片大概率只是重复截图，降到「只描述图」档而不是全量转录"""
    text = ("实验报告\npublic class HelloWorld {\n  public static void main(String[] a){}\n}\n"
            "运行结果：Hello World\n" + "分析。" * 30)
    sig = ocr.vision_needed(text)
    assert sig["signals"]["has_inline_code"] is True
    # 有代码时即便命中「图片型」判据，也应走 caption 档：省成本且不重复计分
    if sig["needed"]:
        assert sig["mode"] == "caption"


def test_vision_needed_transcribes_when_no_code_anywhere():
    """正文里既没有代码也没有输出：必须走转录档，否则整类报告被判低"""
    text = "实验报告\n运行结果\n截图说明：需包含控制台输出截图\n" + "步骤说明。" * 40
    sig = ocr.vision_needed(text)
    assert sig["needed"] is True
    assert sig["mode"] == "transcribe"
    assert sig["signals"]["has_inline_code"] is False


def test_vision_needed_flags_parser_warnings():
    sig = ocr.vision_needed("很短", warnings=["疑似扫描件：正文仅 12 字"])
    assert sig["needed"] is True
    assert any("扫描件" in r for r in sig["reasons"])


def test_vision_needed_flags_embedded_images():
    sig = ocr.vision_needed("正文足够长。" * 50, embedded_images=7)
    assert sig["needed"] is True
    assert sig["signals"]["embedded_images"] == 7


# ---------- 逐页读取 ----------

def test_read_report_images_makes_one_call_per_page(fake_pdf, isolated_cache, monkeypatch):
    client = _Client([
        {"text": "第一页正文", "images": [], "legible": True},
        {"text": "第二页正文", "images": [{"kind": "screenshot",
                                            "caption": "控制台输出", "content": "Hello World"}],
         "legible": True},
        {"text": "第三页正文", "images": [], "legible": True},
    ])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)

    res = ocr.read_report_images(fake_pdf, model="deepseek-flash", use_cache=False)
    assert res["stats"]["pages_read"] == 3
    assert res["stats"]["images"] == 1
    assert res["stats"]["failed"] == 0
    assert len(client.chat.completions.calls) == 3
    # 页码必须落在结果里 —— 它是证据可定位到页的唯一依据
    assert [p["page"] for p in res["pages"]] == [1, 2, 3]


def test_each_page_is_sent_as_its_own_image_block(fake_pdf, isolated_cache, monkeypatch):
    """逐页调用：每页一次请求、一张图，页码归属不会串"""
    client = _Client([{"text": "ok", "images": []} for _ in range(3)])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)

    ocr.read_report_images(fake_pdf, model="m", use_cache=False)
    for call in client.chat.completions.calls:
        content = call["messages"][1]["content"]
        blocks = [c for c in content if c["type"] == "image_url"]
        assert len(blocks) == 1
        assert blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_page_failure_is_isolated(fake_pdf, isolated_cache, monkeypatch):
    """第 2 页调用失败：其余页照常产出，失败页被如实记录，不伪造内容"""
    client = _Client([
        {"text": "第一页", "images": []},
        RuntimeError("boom: 429 rate limited"),
        {"text": "第三页", "images": []},
    ])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)

    res = ocr.read_report_images(fake_pdf, model="m", use_cache=False)
    assert res["stats"]["pages_read"] == 3
    assert res["stats"]["failed"] == 1
    assert len(res["errors"]) == 1 and "第 2 页" in res["errors"][0]
    assert res["pages"][1]["text"] == ""          # 失败页没有内容，也没有凭空生成
    assert res["pages"][0]["text"] == "第一页"


def test_unknown_image_kind_is_normalized(fake_pdf, isolated_cache, monkeypatch):
    client = _Client([{"text": "x", "images": [
        {"kind": "hologram", "caption": "未注册的类型"},
        {"kind": "TABLE", "caption": "大写也应被接受"}]}])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)
    res = ocr.read_report_images(fake_pdf, model="m", use_cache=False)
    kinds = [i["kind"] for i in res["pages"][0]["images"]]
    assert kinds == ["other", "table"]


# ---------- 缓存 ----------

def test_cache_avoids_second_call(fake_pdf, isolated_cache, monkeypatch):
    """假 PDF 会被渲染成 3 页 → 首轮 3 次调用；第二轮全部命中缓存，0 次调用。"""
    client = _Client([{"text": "第%d页" % i, "images": []} for i in (1, 2, 3)])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)

    first = ocr.read_report_images(fake_pdf, model="m")
    assert first["stats"]["cached_hits"] == 0
    assert len(client.chat.completions.calls) == 3

    second = ocr.read_report_images(fake_pdf, model="m")
    assert second["stats"]["cached_hits"] == 3
    assert len(client.chat.completions.calls) == 3     # 命中缓存：没有再调用
    assert all(p["cached"] is True for p in second["pages"])
    assert [p["text"] for p in second["pages"]] == ["第1页", "第2页", "第3页"]


def test_caption_and_transcribe_do_not_share_cache(fake_pdf, isolated_cache, monkeypatch):
    """两种档位产出完全不同，缓存绝不能混用"""
    client = _Client([{"text": "转录内容", "images": []} for _ in range(3)] +
                     [{"text": "", "images": [{"kind": "screenshot", "caption": "控制台"}]}
                      for _ in range(3)])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)

    a = ocr.read_report_images(fake_pdf, model="m", transcribe=True)
    b = ocr.read_report_images(fake_pdf, model="m", transcribe=False)
    assert a["stats"]["cached_hits"] == 0
    assert b["stats"]["cached_hits"] == 0          # 档位不同 → 不命中对方的缓存
    assert b["stats"]["mode"] == "caption"
    assert b["pages"][0]["text"] == ""


def test_changing_prompt_version_invalidates_cache(fake_pdf, isolated_cache, monkeypatch):
    """改了提示词必须让旧缓存失效，否则会出现「代码是新的、产出是旧的」"""
    client = _Client([{"text": "v1", "images": []} for _ in range(3)] +
                     [{"text": "v2", "images": []} for _ in range(3)])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)

    ocr.read_report_images(fake_pdf, model="m")
    monkeypatch.setattr(ocr, "PROMPT_VERSION", "ocr-v2")
    res = ocr.read_report_images(fake_pdf, model="m")
    assert res["stats"]["cached_hits"] == 0
    assert res["pages"][0]["text"] == "v2"


def test_changing_model_invalidates_cache(fake_pdf, isolated_cache, monkeypatch):
    client = _Client([{"text": "a", "images": []} for _ in range(3)] +
                     [{"text": "b", "images": []} for _ in range(3)])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)
    ocr.read_report_images(fake_pdf, model="model-a")
    res = ocr.read_report_images(fake_pdf, model="model-b")
    assert res["stats"]["cached_hits"] == 0


def test_corrupt_cache_file_falls_back_to_calling_model(fake_pdf, isolated_cache, monkeypatch):
    client = _Client([{"text": "正常", "images": []} for _ in range(3)])
    monkeypatch.setattr("llm._get_client", lambda *a, **k: client)
    os.makedirs(isolated_cache, exist_ok=True)
    digest = ocr._file_digest(fake_pdf)
    cp = ocr.cache_path(digest, 1, ocr.DEFAULT_DPI, "m")
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON")

    res = ocr.read_report_images(fake_pdf, model="m")
    # 全新缓存：三页都要真调用；坏掉的那页也必须重新调用而不是静默返回空
    assert res["stats"]["cached_hits"] == 0
    assert res["stats"]["pages_read"] == 3
    assert [p["text"] for p in res["pages"]] == ["正常", "正常", "正常"]


# ---------- 拼进正文 ----------

def test_build_section_text_keeps_page_headings():
    """拼出来的文本必须带「第 N 页」标题，并且与正文之间有空行 ——
    这两点是章节切分器识别页边界的判据。"""
    pages = [
        {"page": 1, "text": "Part 1 开发环境与行业认知\nJDK 下载安装与环境配置",
         "images": []},
        {"page": 2, "text": "程序运行结果如下。",
         "images": [{"kind": "screenshot", "caption": "控制台输出",
                     "content": "Hello World\nBUILD SUCCESS"}]},
    ]
    text = ocr.build_section_text(pages)
    assert "第 1 页 OCR 转录" in text
    assert "第 2 页 OCR 转录" in text
    assert "第 1 页 OCR 转录\n\n" in text        # 标题后必须空行
    # 图片说明必须带类型前缀，模型才不会把它当成学生写的正文
    assert "[截图] 控制台输出" in text
    assert "BUILD SUCCESS" in text


def test_ocr_pages_become_independent_sections():
    """每一页都必须成为**独立章节**：证据要能精确指到「第 N 页」。

    注意用足够长的正文：过短的段会被 parser 按 MIN_SECTION_CHARS 并入上一段，
    那是解析器既有的、正确的合并规则（页标题文字仍在正文里，引用依旧可定位）。
    """
    pages = [{"page": i, "text": f"第 {i} 页的转录正文内容。" * 30, "images": []}
             for i in (1, 2, 3)]
    sections = P.split_sections(ocr.build_section_text(pages))
    titles = [s.title for s in sections]
    for i in (1, 2, 3):
        assert any(f"第 {i} 页 OCR 转录" in t for t in titles), titles
    # 章节文本里包含页标题，等于引用可以写「第 3 页 OCR 转录…」
    assert all(f"第 {i} 页 OCR 转录" in s.text
               for i, s in zip((1, 2, 3), sections[1:]))


def test_enrich_text_keeps_original_prefix():
    """原文必须在前、且一字不改：既有引用的定位不能被 OCR 打乱"""
    original = "原文第一段。\n原文第二段。"
    pages = [{"page": 1, "text": "图片里读出来的内容", "images": []}]
    out = ocr.enrich_text(original, pages)
    assert out.startswith(original)
    assert "图片里读出来的内容" in out
    assert ocr.enrich_text(original, []) == original


def test_enrich_text_marks_unreadable_page_explicitly():
    """读不出来就写「无法辨认」——「看不出内容」不等于「没有内容」"""
    text = ocr.build_section_text([{"page": 4, "text": "",
                                    "images": [{"kind": "screenshot",
                                                "caption": "运行截图",
                                                "content": "无法辨认"}]}])
    assert "无法辨认" in text


def test_empty_pages_produce_no_text():
    assert ocr.build_section_text([]) == ""


# ---------- 与 parser 的接口 ----------

def test_render_for_ocr_reads_plain_image(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    out = ocr.render_for_ocr(str(img))
    assert len(out) == 1 and out[0][0] == 1


def test_render_for_ocr_rejects_unsupported_format(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    assert ocr.render_for_ocr(str(f)) == []


def test_render_pdf_pages_does_not_reference_missing_fitz(monkeypatch, tmp_path):
    """回归测试：render_pdf_pages 曾经引用未导入的 fitz，必然抛 NameError，
    而上游用 except 把它吞成一行 warn —— 于是「原始版面对照」从来没有生效过。"""
    used = {}

    class _Matrix:
        def __init__(self, a, b):
            used["matrix"] = (a, b)

    class _Pixmap:
        def tobytes(self, fmt):
            return b"PNG"

    class _Page:
        def get_pixmap(self, matrix=None):
            used["pixmap"] = matrix
            return _Pixmap()

    class _Doc:
        def __iter__(self):
            return iter([_Page()])

        def close(self):
            pass

    class _Fitz:
        Matrix = _Matrix

        @staticmethod
        def open(path):
            return _Doc()

    monkeypatch.setattr(P, "_open_pdf", lambda path: _Doc())
    monkeypatch.setattr(P, "import_fitz", lambda: _Fitz)

    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4")
    pages = P.render_pdf_pages(str(f), max_pages=1, dpi=144)
    assert pages == [b"PNG"]
    assert used["matrix"] == (2.0, 2.0)      # 144 / 72
