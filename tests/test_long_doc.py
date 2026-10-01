# -*- coding: utf-8 -*-
"""超长文档行为测试（2026-09-23 实测后补）

背景（一次真实事故）：
    S06 是 67779 字的报告，原先超过 4 万字上限、走关键词召回，
    而召回预算最多覆盖全文 31% → r3「核心实现」被判 0 分（人工 14 分），
    总分 27 vs 人工 43，属于**系统性假阴性**。
    改法两条：
      1. 全文上限提到 7 万字，这类文档直接给全文（实测总分 27 → 52）；
      2. 确实超出上限、只能召回时，**所有 miss 一律强制转人工复核**——
         只给模型看了部分正文，"找不到依据"就不能算学生没做到。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import parser as P
import pipeline
from models import ItemJudgement, Feedback, Rubric, RubricItem

LIMIT = P.FULL_TEXT_LIMIT
BASE = "本实验报告写清了实验目的，给出完整实现过程与运行结果数据，并做了分析总结。" * 3
ITEM = RubricItem(id="r1", name="核心实现", criteria="是否有实现过程", max_score=20)


UNIT = "补充说明与实验记录。"


def _text_of(n: int) -> str:
    """构造恰好 n 个字符的正文（用来精确卡在全文上限的边界上）"""
    s = BASE
    while len(s) < n:
        s += UNIT
    return s[:n]


def test_build_context_gives_full_text_at_limit():
    text = _text_of(LIMIT)
    assert len(text) == LIMIT
    ctx, is_full = P.build_context([], text, ["实现"], top_k=6)
    assert is_full is True
    assert ctx == text, "未超上限必须给全文，而不是召回"


def test_build_context_falls_back_to_recall_beyond_limit():
    text = _text_of(LIMIT + 3000)
    assert len(text) > LIMIT
    ctx, is_full = P.build_context([], text, ["实现"], top_k=6)
    assert is_full is False
    assert len(ctx) < len(text)


def test_coverage_flag_reflects_mode():
    assert P.inspect_text(_text_of(LIMIT))["coverage"] == "full"
    assert P.inspect_text(_text_of(LIMIT + 3000))["coverage"] == "retrieved"


def _install_mocks(monkeypatch, verdict="miss", score=0.0):
    def fake_call(system, user, schema, temperature=0.0, **kw):
        if schema is ItemJudgement:
            return ItemJudgement(rubric_item_id="r1", verdict=verdict, score=score,
                                 confidence=0.9, reason="没找到依据",
                                 evidence=[])
        if schema is Feedback:
            return Feedback(summary="小结", suggestions=["建议"])
        raise AssertionError(f"未预期的 schema：{schema}")
    monkeypatch.setattr(pipeline, "call_json", fake_call)


def test_recall_mode_miss_is_forced_to_human_review(monkeypatch):
    """召回模式下判 miss：必须转人工——这是"没看到 ≠ 没做到"的机器化表达"""
    _install_mocks(monkeypatch, verdict="miss")
    text = _text_of(LIMIT + 3000)
    res = pipeline.run_grading(text, [P.Section(id="s1", title="正文", text=text[:2000],
                                                char_start=0, char_end=2000)],
                               "", report_id="T-LONG", rubric=Rubric(items=[ITEM]),
                               enable_recheck=False)
    j = res.judgements[0]
    assert j.verdict == "miss"
    assert j.needs_review is True, "召回模式下的 miss 必须转人工"
    assert "召回" in j.reason
    assert res.run_info.parse_coverage == "retrieved"


def test_full_text_mode_miss_is_not_auto_flagged(monkeypatch):
    """全文模式下判 miss 是可信的（模型看到了全部内容），不该一律转人工"""
    _install_mocks(monkeypatch, verdict="miss")
    text = _text_of(LIMIT - 200)
    res = pipeline.run_grading(text, [P.Section(id="s1", title="正文", text=text[:2000],
                                                char_start=0, char_end=2000)],
                               "", report_id="T-FULL", rubric=Rubric(items=[ITEM]),
                               enable_recheck=False)
    assert res.run_info.parse_coverage == "full"
    assert res.judgements[0].needs_review is False
