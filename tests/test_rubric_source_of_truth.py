# -*- coding: utf-8 -*-
"""固定 5 项评分标准必须**只有一份定义**

这个测试守的是一个不报错的隐患，而不是一个会崩的 bug。

背景：同一套"固定 5 项标准"曾经在四个地方各写了一份 ——
`pipeline` / `batch_ui` 的兜底 / `tools/batch_run.py` / `tools/make_demo.py`，
而且它们的**信号词并不一致**：
- `pipeline` 那份多了「了解」「版本」「类」「方法」「运行」「复杂度」；
- `batch_ui` 的兜底那份每项只有 4 个词（少了「旨在」「命令」「表」「讨论」）。

危险在于人工 gold set 是按 `tools/batch_run.py` 那份打的：
一旦有人改了"看起来一样"的另一份，离线规则通道的判定就会与对外公布的基准口径
悄悄脱钩。**它不报错，只是数字慢慢对不上** —— 这种问题最难查，
所以在收敛之后必须有测试把它钉住。
"""
import os
import sys
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import batch_ui
import pipeline as PL


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _signature(items):
    """把一份 rubric 压成可比较的指纹（只含会真正影响判定的字段）"""
    return [(i.id, i.name, i.criteria, float(i.max_score), tuple(i.positive_signals))
            for i in items]


def test_all_fixed_rubric_sources_are_identical():
    batch_run = _load(os.path.join("tools", "batch_run.py"), "br_probe")
    make_demo = _load(os.path.join("tools", "make_demo.py"), "md_probe")

    sources = {
        "pipeline.fixed_rubric_items()": PL.fixed_rubric_items(),
        "batch_ui.fixed_rubric()": batch_ui.fixed_rubric().items,
        "tools/batch_run.ITEMS": batch_run.ITEMS,
        "tools/make_demo.ITEMS": make_demo.ITEMS,
    }
    ref = _signature(PL.fixed_rubric_items())
    for label, items in sources.items():
        assert _signature(items) == ref, (
            f"{label} 与 pipeline 的固定标准不一致 —— "
            f"人工 gold 是按同一套标准打的，两份并存会让对外口径悄悄脱钩")


def test_fixed_rubric_totals_exactly_100():
    """合计必须正好 100：validate_rubric 会断言这一点，标准本身也要自洽"""
    items = PL.fixed_rubric_items()
    assert len(items) >= 3, "评分点太少，区分度无从谈起"
    assert abs(sum(i.max_score for i in items) - 100.0) < 1e-9


def test_every_fixed_item_has_a_usable_signal_set():
    """每项都要有信号词：离线规则通道完全依赖它，缺了就等于该项永远判 miss"""
    for i in PL.fixed_rubric_items():
        assert i.positive_signals, f"{i.id} 没有信号词，离线通道会永远判它 miss"
        assert all(s.strip() for s in i.positive_signals), f"{i.id} 有空白信号词"


def test_fixed_rubric_returns_fresh_objects():
    """每次都要是新对象：界面会把 rubric 改脏，共用同一批实例会串改全局标准"""
    a = PL.fixed_rubric_items()
    b = PL.fixed_rubric_items()
    assert a is not b
    assert a[0] is not b[0]
    a[0].max_score = 999.0
    assert b[0].max_score != 999.0, "改动一个副本污染了另一个 —— 说明共用实例"
    # 模块级常量也不能被污染
    assert PL.DEFAULT_RUBRIC_ITEMS[0][3] == 15


def test_batch_ui_fixed_rubric_is_independent_per_call():
    r1 = batch_ui.fixed_rubric()
    r2 = batch_ui.fixed_rubric()
    assert r1.items[0] is not r2.items[0]
    r1.items[0].max_score = 999.0
    assert r2.items[0].max_score != 999.0
