# -*- coding: utf-8 -*-
"""版面渲染（原始页面对照）测试

边界：本功能只做"看得见"，不产生文本、不进证据链，因此测试也只断言这一点。
"""
import glob
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import parser as P

PNG_SIG = b"\x89PNG\r\n\x1a\n"

#: 随仓库发布的测试夹具（自造内容，可安全公开）。
#: 为什么必须有它：本测试原先只找 `data/raw/*.pdf`（本机私有样本，已 gitignore），
#: 于是**在干净克隆与 CI 里永远 skip** —— 而它守的正是「版面渲染」这条链路，
#: 那条链路曾经因为一个未导入的 `fitz` 静默失败了很久都没人发现。
#: 关键路径的测试不允许依赖本机私有数据。
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
CANDIDATES = [
    os.path.join(FIXTURES, "*.pdf"),                                  # 仓库内置夹具（首选）
    os.path.join(ROOT, "data", "raw", "*.pdf"),                       # 本机私有原始样本
]


def _any_pdf():
    for pattern in CANDIDATES:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def test_non_pdf_returns_empty(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("纯文本", encoding="utf-8")
    assert P.render_pdf_pages(str(txt)) == []
    assert P.pdf_page_count(str(txt)) == 0


def test_zero_max_pages_returns_empty(tmp_path):
    assert P.render_pdf_pages(str(tmp_path / "x.pdf"), max_pages=0) == []


def test_missing_file_raises_explicitly(tmp_path):
    """预览失败要能被发现，不能静默返回空（界面层会捕获并提示）"""
    with pytest.raises(FileNotFoundError):
        P.render_pdf_pages(str(tmp_path / "nope.pdf"))


@pytest.mark.skipif(_any_pdf() is None, reason="没有可用的 PDF（含仓库内置夹具，不应发生）")
def test_renders_png_pages_with_cap():
    pdf = _any_pdf()
    pages = P.render_pdf_pages(pdf, max_pages=2)
    assert 1 <= len(pages) <= 2
    for b in pages:
        assert b[:8] == PNG_SIG, "必须是 PNG 字节"
        assert len(b) > 1000, "页面图不应该小到不可读"
    total = P.pdf_page_count(pdf)
    assert total >= len(pages)


def test_repo_fixture_exists_so_this_suite_never_skips():
    """仓库必须自带 PDF 夹具：否则上面两条测试在干净克隆里永远 skip。

    这条断言本身就是"防回归"——有人清理仓库时顺手删掉夹具，它会立刻失败，
    而不是让关键路径的测试悄悄失去覆盖。
    """
    fx = sorted(glob.glob(os.path.join(FIXTURES, "*.pdf")))
    assert fx, f"tests/fixtures 下没有 PDF 夹具：{FIXTURES}"


@pytest.mark.skipif(_any_pdf() is None, reason="没有可用的 PDF")
def test_rendering_produces_no_text_and_no_files(tmp_path):
    """渲染只返回内存字节：不产生文本、不落盘"""
    pdf = _any_pdf()
    before = set(glob.glob(os.path.join(tmp_path, "*")))
    pages = P.render_pdf_pages(pdf, max_pages=1)
    assert pages and isinstance(pages[0], bytes)
    assert set(glob.glob(os.path.join(tmp_path, "*"))) == before
    # 渲染结果里不应混入任何文本（它是图片字节）
    assert "实验目的".encode("utf-8") not in pages[0]


def test_caption_states_the_boundary():
    """文案必须讲清「看得见但读不到」，否则会变成虚假宣称"""
    one = P.preview_caption(9, 9)
    assert "9 页" in one
    assert "仅供人工对照" in one
    assert "未开启 OCR" in one
    many = P.preview_caption(20, 33)
    assert "共 33 页" in many and "仅渲染前 20 页" in many


def test_caption_flips_when_ocr_is_on():
    """开了 OCR 就不能再说「系统不做 OCR」——那会变成自相矛盾的假话"""
    off = P.preview_caption(9, 9, ocr_on=False)
    on = P.preview_caption(9, 9, ocr_on=True)
    assert "未开启 OCR" in off
    assert "已由多模态 OCR 转录" in on
    assert "未开启 OCR" not in on
    # 两个档位都必须提醒老师以原始版面为准（转录可能有误）
    assert "人工对照" in off and "人工对照" in on
