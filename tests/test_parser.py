# -*- coding: utf-8 -*-
"""解析器测试（不调用模型）"""
import os
import sys

import parser as P

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "samples")


def test_canonical_collapses_whitespace():
    assert P.canonical("a  b\n\nc") == "a b c"


# ---------- 逐字加空格（字距 artifact）的归一 ----------
#
# 实测来源：PDF 里中英混排常被渲染成字距很宽的形态，多模态 OCR 会照着图上的间隔
# **原样转录**（提示词要求"原样转录"，它执行得很忠实）。于是模型给的 quote 带字距，
# 而正文是紧凑写法，逐字校验直接失败 —— 引用被作废，而真实证据其实就在原文里。

def test_collapses_letter_spacing_in_words():
    assert P.collapse_char_spacing("f a l s e") == "false"
    assert P.collapse_char_spacing("J a v a") == "Java"
    assert P.collapse_char_spacing("b o o l e a n") == "boolean"
    assert P.collapse_char_spacing("取款失败：f a l s e") == "取款失败：false"


def test_does_not_merge_intentional_sequences():
    """全大写缩写与短并列写法**不能**被合并 —— 那是并列写法，不是字距 artifact。

    `A B C D` 合并成 `ABCD` 就篡改了原文，比漏合并一条引用严重得多。
    """
    assert P.collapse_char_spacing("A B C D") == "A B C D"
    assert P.collapse_char_spacing("I O") == "I O"
    assert P.collapse_char_spacing("a b c") == "a b c"
    assert P.collapse_char_spacing("a b") == "a b"
    assert P.collapse_char_spacing("选项 A B") == "选项 A B"


def test_merges_uniform_digit_runs():
    """逐位拆开的数字串（整齐形态）要合并。

    本规则只处理**整齐的**逐字符拆开（每个 token 恰好一个字符、间距一致），
    因为那才是字距 artifact 的形态。参差不齐的 token（`0 . 0 3 8` 里小数点
    自己占一个 token）不在处理范围内 —— 与其把规则复杂化去猜，不如明确边界：
    归一只负责消掉可判定的 artifact，猜不出来的留给证据校验去重跑。
    """
    assert P.collapse_char_spacing("1 5 0 0") == "1500"
    assert P.collapse_char_spacing("2 0 2 6") == "2026"
    # 单个数字是正常写法，不能动
    assert P.collapse_char_spacing("共执行 4 次，成功 3 次") == "共执行 4 次，成功 3 次"


def test_does_not_merge_ragged_number_columns():
    """位数不齐的数据列不能被合并：那才是真实报告里「一行若干数据」的形态"""
    assert P.collapse_char_spacing("12 34 567 8") == "12 34 567 8"
    assert P.collapse_char_spacing("2026 10 02") == "2026 10 02"


def test_drops_spaces_between_cjk():
    """中文之间无论几个空格都删掉：字间空格在中文里没有语义"""
    assert P.collapse_char_spacing("实 验 目 的") == "实验目的"
    assert P.collapse_char_spacing("实验  目的") == "实验目的"


def test_char_spacing_normalization_keeps_evidence_verifiable():
    """端到端意义：带字距的引用必须能通过逐字校验，否则整条判定会被作废"""
    import llm
    from models import ItemJudgement, Evidence
    text = "取款金额超过余额时方法返回 false 且余额保持不变。"
    quote = "取款金额超过余额时方法返回 f a l s e 且余额保持不变。"
    j = ItemJudgement(rubric_item_id="r1", verdict="hit", score=10, confidence=0.9,
                      reason="x", evidence=[Evidence(section_id="", quote=quote,
                                                     char_start=-1)])
    assert llm.verify_evidence(j, text) is True


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
