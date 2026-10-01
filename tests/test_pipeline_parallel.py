# -*- coding: utf-8 -*-
"""A 阶段并发 —— 顺序正确性是这里的头号风险

把「逐评分点串行调用」改成并发后，最危险的不是报错，而是**静默错配**：
每个评分点的耗时不同（有的要重跑一次判定），如果回填时用「完成顺序」
而不是「原始下标」，第 1 个评分点的判定就会被贴到第 3 个上。

这种错误不抛异常、不降分，只是每条理由都跟它对面的评分点对不上号。
老师看到的分数总额完全正常，所以**一旦发生就会被当成正确结果采信**。
必须钉死。

测试手法：让替身的耗时随机，并且把返回值**绑定在评分点名字上**。
这样只要发生串位，分数与评分点就对不上，断言立刻失败。
"""
import random
import re
import time

import pytest

import pipeline
from models import Rubric, RubricItem, Feedback

FULL = ("本报告写清了实验目的，并给出了完整的实现过程与运行结果数据，"
        "最后做了分析总结。")
GOOD = "完整的实现过程"

N = 6


def _items(n=N):
    return [RubricItem(id=f"r{i}", name=f"点{i}", criteria="是否有内容",
                       max_score=10) for i in range(n)]


def _make_fake(delay=True):
    """替身：按**评分点名字**返回对应分数，耗时随机。"""
    def fake(system, user, schema, **kw):
        if schema is Feedback:
            return Feedback(summary="ok", per_item={}, suggestions=[])
        m = re.search(r"评分点：(\S+)", user)
        name = m.group(1) if m else ""
        idx = int(name.replace("点", "")) if re.match(r"^点\d+$", name) else 0
        if delay:
            time.sleep(random.uniform(0.01, 0.06))     # 耗时不同，逼出乱序
        return schema.model_validate({
            "verdict": "hit", "score": idx + 1, "confidence": 0.9,
            "reason": f"{name}的理由",
            "evidence": [{"quote": GOOD}],
        })
    return fake


@pytest.mark.parametrize("workers", [1, 2, 4, 8])
def test_parallel_preserves_item_order(monkeypatch, workers):
    """无论并发度多少，判定结果都必须与评分点一一对应。"""
    monkeypatch.setattr(pipeline, "call_json", _make_fake())
    res = pipeline.run_grading(FULL, [], raw_rubric="", rubric=Rubric(items=_items()),
                               enable_recheck=False, judge_workers=workers)
    assert [j.rubric_item_id for j in res.judgements] == [f"r{i}" for i in range(N)]
    assert [j.score for j in res.judgements] == [i + 1 for i in range(N)], \
        "判定与评分点错位了 —— 并发回填用错了下标"


def test_parallel_actually_runs_in_parallel(monkeypatch):
    """不只是「没出错」，而要有真实收益：总耗时应显著小于串行。"""
    monkeypatch.setattr(pipeline, "call_json", _make_fake())

    kw = dict(raw_rubric="", rubric=Rubric(items=_items(6)),
              enable_recheck=False)

    t0 = time.time()
    pipeline.run_grading(FULL, [], judge_workers=1, **kw)
    t_serial = time.time() - t0

    t0 = time.time()
    pipeline.run_grading(FULL, [], judge_workers=6, **kw)
    t_para = time.time() - t0

    assert t_para < t_serial * 0.6, (
        f"并发没起到加速作用：串行 {t_serial:.2f}s / 并发 {t_para:.2f}s")


def test_implementation_does_not_rely_on_completion_order(monkeypatch):
    """反向对照（诚信底线）：把线程池换成「倒序产出」的假实现，顺序仍必须正确。

    为什么必须有这一条：若 run_grading 写成了「按完成顺序 append」，
    那么当各评分点耗时接近（替身不 sleep 时正是如此）上面的断言**照样全绿**
    —— 那就是一盏假绿灯。这里强制倒序产出最坏情况，逼出实现是否真的
    按原始下标回填。若它挂了，说明上面的顺序断言根本没在守护任何东西。
    """
    import concurrent.futures as cf

    class ShuffledExecutor:
        def __init__(self, max_workers=None):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def map(self, fn, iterable):
            for x in reversed(list(iterable)):      # 倒序产出
                yield fn(x)

    monkeypatch.setattr(cf, "ThreadPoolExecutor", ShuffledExecutor)
    monkeypatch.setattr(pipeline, "call_json", _make_fake(delay=False))
    res = pipeline.run_grading(FULL, [], raw_rubric="", rubric=Rubric(items=_items()),
                               enable_recheck=False, judge_workers=4)
    assert [j.score for j in res.judgements] == [i + 1 for i in range(N)], \
        "实现依赖了完成顺序 —— 一旦各评分点耗时不同就会张冠李戴"


def test_worker_count_is_clamped_to_item_count(monkeypatch):
    """并发度不该超过评分点数量（开多余线程只会平白增加限流风险）。"""
    monkeypatch.setattr(pipeline, "call_json", _make_fake(delay=False))
    res = pipeline.run_grading(FULL, [], raw_rubric="",
                               rubric=Rubric(items=_items(2)),
                               enable_recheck=False, judge_workers=16)
    assert len(res.judgements) == 2
    assert [j.score for j in res.judgements] == [1, 2]


def test_recheck_failure_marks_review_instead_of_crashing(monkeypatch):
    """并发下更要守住：一致性复核挂了，判定结果仍然有效，只是标未复核。

    顺带补了并发之前就存在的一个缺口 —— 原来 stage_consistency 的异常
    没有兜底，会一路抛到 run_grading 之上把整份评阅打断。
    """
    calls = {"n": 0}

    def fake(system, user, schema, **kw):
        if schema is Feedback:
            return Feedback(summary="ok", per_item={}, suggestions=[])
        m = re.search(r"评分点：(\S+)", user)
        name = m.group(1) if m else ""
        idx = int(name.replace("点", "")) if re.match(r"^点\d+$", name) else 0
        payload = {"verdict": "hit", "score": idx + 1, "confidence": 0.9,
                   "reason": "理由", "evidence": [{"quote": GOOD}]}
        # 一致性复核（S3）故意抛异常
        if "复核" in system or "S3" in system:
            raise RuntimeError("复核服务不可用")
        calls["n"] += 1
        return schema.model_validate(payload)

    monkeypatch.setattr(pipeline, "call_json", fake)
    res = pipeline.run_grading(FULL, [], raw_rubric="",
                               rubric=Rubric(items=_items(3)),
                               enable_recheck=True, judge_workers=3)
    assert len(res.judgements) == 3
    assert [j.score for j in res.judgements] == [1, 2, 3], "判定结果必须保住"
    assert all(j.needs_review for j in res.judgements), "未复核必须标出来给人"
