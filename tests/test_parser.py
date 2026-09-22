# -*- coding: utf-8 -*-
"""解析器测试（不调用模型）"""
import os
import sys

import parser as P

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "samples")


def test_canonical_collapses_whitespace():
    assert P.canonical("a  b\n\nc") == "a b c"


def test_heading_before_content_not_lost():
    text = "封面与摘要内容，足够长以避免被合并掉。\n实验目的\n这里是实验目的的正文内容。"
    secs = P.split_sections(text)
    assert any("封面" in s.text for s in secs), "第一个标题之前的正文不能丢"


def test_merged_sections_have_real_offsets():
    text = "一、实验目的\n" + ("目的正文。" * 10) + "\n二、实验步骤\n" + ("步骤正文。" * 10)
    secs = P.split_sections(text)
    for s in secs:
        assert text.find(s.text) == s.char_start, "char_start 必须等于真实位置"
        assert s.char_end == s.char_start + len(s.text)
    assert all(secs[i].char_start <= secs[i + 1].char_start for i in range(len(secs) - 1))


def test_fallback_chunks_offsets():
    text = "x" * 2500
    secs = P._fallback_chunks(text)
    assert secs[0].char_start == 0
    assert secs[-1].char_end == len(text)
    for s in secs:
        assert text[s.char_start:s.char_end] == s.text


def test_empty_text_flagged():
    info = P.inspect_text("")
    assert info["coverage"] == "empty"
    assert any("未提取到任何文本" in w for w in info["warnings"])


def test_short_text_flagged_as_scan():
    info = P.inspect_text("只有几个字")
    assert any("疑似扫描件" in w for w in info["warnings"])


def test_long_text_flagged_as_retrieval():
    info = P.inspect_text("字" * (P.FULL_TEXT_LIMIT + 100))
    assert info["coverage"] == "retrieved"
    assert any("召回" in w for w in info["warnings"])


def test_real_sample_sections_are_consistent():
    """有样本时做一次真实校验：每段都能在全文里找到，偏移合法"""
    path = os.path.join(SAMPLE, "S08.txt")
    if not os.path.exists(path):
        return
    full, secs = P.parse_file(path)
    assert all(s.text in full for s in secs if s.text)
    assert all(0 <= s.char_start <= s.char_end <= len(full) for s in secs)
