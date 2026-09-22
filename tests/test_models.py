# -*- coding: utf-8 -*-
"""数据契约与 rubric 校验测试（不调用模型）"""
import math

import pytest

from models import Rubric, RubricItem, ItemJudgement, Evidence, HumanOverride
from pipeline import validate_rubric, RubricError, rubric_source_hash


def item(iid="r1", score=20.0, **kw):
    return RubricItem(id=iid, name=kw.get("name", "名称"), criteria="标准", max_score=score)


def test_legal_rubric_passes():
    r = validate_rubric(Rubric(items=[item("r1", 40), item("r2", 60)]))
    assert round(sum(i.max_score for i in r.items), 2) == 100


def test_empty_rubric_rejected():
    with pytest.raises(Exception):
        validate_rubric(Rubric(items=[]))


def test_duplicate_id_rejected():
    with pytest.raises(RubricError) as e:
        validate_rubric(Rubric(items=[item("r1", 50), item("r1", 50)]))
    assert "重复" in str(e.value)


def test_negative_and_nan_rejected():
    with pytest.raises(Exception):
        validate_rubric(Rubric(items=[item("r1", -5)]))
    with pytest.raises(Exception):
        validate_rubric(Rubric(items=[item("r1", float("nan"))]))
    with pytest.raises(Exception):
        validate_rubric(Rubric(items=[item("r1", float("inf"))]))


def test_empty_id_rejected():
    with pytest.raises(Exception):
        item(" ")


def test_thirteen_items_still_sums_to_100():
    """旧 bug：13 项各 1 分，先归一再截断会得到 12 项合计 92.3 分"""
    items = [item(f"r{i}", 1.0) for i in range(1, 14)]
    r = validate_rubric(Rubric(items=items))
    assert len(r.items) == 12
    assert abs(sum(i.max_score for i in r.items) - 100) < 0.05


def test_normalize_keeps_100():
    r = validate_rubric(Rubric(items=[item("r1", 7), item("r2", 11), item("r3", 13)]))
    assert abs(sum(i.max_score for i in r.items) - 100) < 0.05


def test_source_hash_stable_and_sensitive():
    assert rubric_source_hash("a", "b") == rubric_source_hash("a", "b")
    assert rubric_source_hash("a", "b") != rubric_source_hash("a", "c")
    assert rubric_source_hash("a", "b") != rubric_source_hash("a2", "b")


def test_judgement_defaults_do_not_carry_total():
    j = ItemJudgement(verdict="hit", score=10, confidence=0.9)
    assert not hasattr(j, "total"), "模型输出里不允许出现总分字段"


def test_human_override_fields():
    o = HumanOverride(rubric_item_id="r1", original_score=10, new_score=15,
                      reason="教师复核后改分", created_at="2026-09-22T00:00:00")
    assert o.new_score == 15 and o.reason
