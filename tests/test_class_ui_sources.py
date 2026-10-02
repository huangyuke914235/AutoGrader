# -*- coding: utf-8 -*-
"""班级看板的数据来源解析

要守的两件事：
1. **命令行跑出来的批量结果，回到界面就能看** —— 教师跑完 `tools/batch_run.py`
   不该还要手动把文件拷到某个目录去。这个测试确保 CLI 产物形状能被识别。
2. **不是队列的文件不能列进下拉框** —— `data/class_demo/` 下还放着
   `ocr_comparison.json`（读图前后对照），它不是队列；列出来只会让人选到一个
   打不开的东西，而那种"选了没反应"的体验最容易被当成 bug。
"""
import json
import os

import class_ui


def _cohort_reports():
    p = os.path.join(class_ui.ROOT, "data", "class_demo", "cohort.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)["reports"][:4]


def test_reads_batch_run_shape(tmp_path):
    """CLI 产物：明细在 results 里"""
    p = tmp_path / "batch_v2_probe.json"
    p.write_text(json.dumps({"version": "v2", "config": {"course": "探测"},
                             "results": _cohort_reports()}, ensure_ascii=False),
                 encoding="utf-8")
    got = class_ui._read_batch_run(str(p))
    assert got is not None
    assert len(got["reports"]) == 4


def test_reads_cohort_shape(tmp_path):
    """队列产物：明细在 reports 里，且带 course"""
    p = tmp_path / "cohort.json"
    p.write_text(json.dumps({"engine": "model", "course": "Java",
                             "reports": _cohort_reports()}, ensure_ascii=False),
                 encoding="utf-8")
    got = class_ui._read_batch_run(str(p))
    assert got is not None and got["course"] == "Java"


def test_course_is_read_from_either_location(tmp_path):
    """`course` 有两个可能位置：CLI 产物在 config 里，队列文件在顶层。

    回归点：只认 config 时，队列来源的课程名会悄悄变成空串 ——
    界面上少一行说明、导出的留档也少了上下文，而**不会有任何报错**。
    """
    # CLI 形状：config.course
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"config": {"course": "命令行课程"},
                             "results": _cohort_reports()}, ensure_ascii=False),
                 encoding="utf-8")
    assert class_ui._read_batch_run(str(a))["course"] == "命令行课程"

    # 队列形状：顶层 course
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"course": "队列课程",
                             "reports": _cohort_reports()}, ensure_ascii=False),
                 encoding="utf-8")
    assert class_ui._read_batch_run(str(b))["course"] == "队列课程"

    # 两处都有时以 config 为准（CLI 产物更明确）
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"config": {"course": "优先"},
                             "course": "次要",
                             "results": _cohort_reports()}, ensure_ascii=False),
                 encoding="utf-8")
    assert class_ui._read_batch_run(str(c))["course"] == "优先"

    # 都没有时为空串，但不能炸
    d = tmp_path / "d.json"
    d.write_text(json.dumps({"results": _cohort_reports()}, ensure_ascii=False),
                 encoding="utf-8")
    assert class_ui._read_batch_run(str(d))["course"] == ""


def test_rejects_json_that_is_not_a_cohort(tmp_path):
    """普通的 JSON（没有明细）不能被当成队列"""
    for payload in ({"before": {}, "after": {}},      # ocr_comparison.json 的形状
                    {"results": []},
                    {"results": [{"report_id": "S1"}]},   # 有报告但无明细
                    {"foo": "bar"}):
        p = tmp_path / "x.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert class_ui._read_batch_run(str(p)) is None, payload


def test_rejects_corrupt_or_missing_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ 这不是合法 JSON", encoding="utf-8")
    assert class_ui._read_batch_run(str(bad)) is None
    assert class_ui._read_batch_run(str(tmp_path / "nope.json")) is None


def test_ocr_comparison_is_not_offered_as_a_source():
    """回归：读图对照文件曾经出现在下拉框里"""
    labels = [label for label, _ in class_ui.available_sources()]
    assert not any("ocr_comparison" in x for x in labels), labels
    # 演示队列必须仍然在
    assert any("演示班级" in x for x in labels), labels


def test_load_source_handles_both_shapes(tmp_path):
    p = tmp_path / "batch_v2_probe.json"
    p.write_text(json.dumps({"config": {}, "results": _cohort_reports()},
                            ensure_ascii=False), encoding="utf-8")
    got = class_ui.load_source(str(p))
    assert len(got["reports"]) == 4

    c = os.path.join(class_ui.ROOT, "data", "class_demo", "cohort.json")
    got2 = class_ui.load_source(c)
    assert got2["reports"], "队列文件也要能读"
