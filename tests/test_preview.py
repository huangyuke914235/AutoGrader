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
CANDIDATES = [
    os.path.join(ROOT, "data", "raw", "*.pdf"),                       # 主仓库的本地原始样本
    os.path.join(os.path.dirname(ROOT), "新建文件夹 (3)",              # 旧目录里的备份样本
                 "autograder", "data", "raw", "*.pdf"),
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


@pytest.mark.skipif(_any_pdf() is None, reason="本机没有可用的原始 PDF 样本")
def test_renders_png_pages_with_cap():
    pdf = _any_pdf()
    pages = P.render_pdf_pages(pdf, max_pages=2)
    assert 1 <= len(pages) <= 2
    for b in pages:
        assert b[:8] == PNG_SIG, "必须是 PNG 字节"
        assert len(b) > 1000, "页面图不应该小到不可读"
    total = P.pdf_page_count(pdf)
    assert total >= len(pages)


@pytest.mark.skipif(_any_pdf() is None, reason="本机没有可用的原始 PDF 样本")
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
    assert "9 页" in one and "不参与自动判定" in one and "OCR" in one
    many = P.preview_caption(20, 33)
    assert "共 33 页" in many and "仅渲染前 20 页" in many
