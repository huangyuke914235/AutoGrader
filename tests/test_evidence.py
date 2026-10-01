# -*- coding: utf-8 -*-
"""证据校验与降级分支测试（P0-1，不调用模型）"""
from models import ItemJudgement, Evidence, RubricItem
from llm import verify_evidence, normalize_judgement, split_evidence
from pipeline import degrade_for_evidence_failure, system_error_judgement

FULL = "本报告包含 实验目的 与完整的实现过程说明，并给出了运行结果与数据分析。"
ITEM = RubricItem(id="r1", name="实现", criteria="是否有实现", max_score=20)


def jd(verdict="hit", score=15, conf=0.9, quotes=("完整的实现过程说明",)):
    return ItemJudgement(verdict=verdict, score=score, confidence=conf,
                         evidence=[Evidence(quote=q) for q in quotes])


def test_valid_evidence_passes():
    j = jd()
    assert verify_evidence(j, FULL) is True
    assert [e.quote for e in j.evidence] == ["完整的实现过程说明"]


def test_miss_must_be_zero_and_no_evidence():
    """旧 bug：miss 可以带正分和伪造证据，还能绕过校验"""
    j = jd(verdict="miss", score=20, quotes=("凭空捏造的一句话啊",))
    j, notes = normalize_judgement(j, ITEM, FULL)
    assert j.score == 0
    assert verify_evidence(j, FULL) is True      # miss 直接放行
    assert j.evidence == []                      # 但证据必须清空


def test_non_miss_without_evidence_fails():
    j = jd(quotes=())
    assert verify_evidence(j, FULL) is False


def test_fabricated_evidence_dropped():
    j = jd(quotes=("这句话根本不在报告里啊", "完整的实现过程说明"))
    assert verify_evidence(j, FULL) is True
    assert [e.quote for e in j.evidence] == ["完整的实现过程说明"]
    assert len(j.dropped_quotes) == 1


def test_all_evidence_fake_fails_and_keeps_record():
    j = jd(quotes=("凭空捏造的一句话啊", "另一句凭空捏造的话啊"))
    assert verify_evidence(j, FULL) is False
    # 全部记进 dropped，同时清空 evidence：分母既不会重复计数，也不会漏掉最差的那批引用
    assert len(j.dropped_quotes) == 2
    assert j.evidence == []


def test_degraded_judgement_keeps_rejected_quotes_for_metrics():
    j = jd(quotes=("凭空捏造的一句话啊",))
    verify_evidence(j, FULL)
    safe = degrade_for_evidence_failure(j, ITEM)
    assert safe.evidence == []
    assert safe.dropped_quotes == ["凭空捏造的一句话啊"], "被作废的引用必须计入可溯源率的分母"


def test_too_short_quote_dropped():
    j = jd(quotes=("实验目的", "完整的实现过程说明"))
    verify_evidence(j, FULL)
    assert [e.quote for e in j.evidence] == ["完整的实现过程说明"]


def test_too_long_quote_dropped():
    long_q = "很长的原文" * 45          # 225 字，超过 QUOTE_MAX_LEN(200) 硬上限
    long_full = long_q + " 尾部"
    j = jd(quotes=(long_q,))
    good, dropped = split_evidence(j, long_full)
    assert good == [] and len(dropped) == 1


def test_degrade_creates_clean_object():
    """降级必须新建一个干净对象，而不是在旧对象上改字段（旧实现会留下无效 evidence）"""
    j = jd(quotes=("凭空捏造的一句话啊",))
    safe = degrade_for_evidence_failure(j, ITEM)
    assert safe.verdict == "miss"
    assert safe.score == 0
    assert safe.evidence == []
    assert safe.needs_review is True
    assert j.evidence, "原对象不应被就地清空（保留审计信息）"


def test_system_error_is_not_student_fault():
    j = system_error_judgement(ITEM, "调用失败：Timeout")
    assert j.system_error
    assert j.needs_review is True
    assert j.score == 0 and j.evidence == []


def test_confidence_and_score_clamped():
    j = jd(score=999, conf=5)
    j, notes = normalize_judgement(j, ITEM, FULL)
    assert j.score <= ITEM.max_score
    assert 0.0 <= j.confidence <= 1.0


def test_illegal_verdict_becomes_partial_and_review():
    j = ItemJudgement(verdict="excellent", score=20, confidence=0.9,
                      evidence=[Evidence(quote="完整的实现过程说明")])
    j, notes = normalize_judgement(j, ITEM, FULL)
    assert j.verdict == "partial"
    assert j.needs_review is True


def test_verdict_score_contradiction_flagged_not_silently_fixed():
    """hit 却只给 1 分：只标记待复核，不偷偷改分"""
    j = jd(verdict="hit", score=1)
    j, notes = normalize_judgement(j, ITEM, FULL)
    assert j.needs_review is True
    assert j.score == 1, "不允许静默改分，只能标记让教师复核"
