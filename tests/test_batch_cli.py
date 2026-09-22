# -*- coding: utf-8 -*-
"""批量评测与 benchmark 的 CLI 冒烟（mock 掉模型调用）"""
import glob
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from models import (Rubric, RubricItem, ItemJudgement, Evidence,
                    GradingResult, Feedback, RunInfo)


def fake_grading(full_text, sections, raw_rubric, report_id="", course_hint="",
                 rubric=None, enable_recheck=True, progress=None):
    """假的 run_grading：给每个评分点一个带合法证据的判定"""
    items = (rubric or Rubric(items=[RubricItem(id="r1", name="a", criteria="c",
                                                max_score=100)])).items
    jds = []
    for it in items:
        q = full_text[2:22] if len(full_text) > 30 else ""
        jds.append(ItemJudgement(rubric_item_id=it.id, verdict="hit",
                                 score=it.max_score, confidence=0.95, reason="ok",
                                 evidence=[Evidence(quote=q)] if len(q) >= 6 else []))
    return GradingResult(report_id=report_id, items=items, judgements=jds,
                         total=sum(j.score for j in jds),
                         ai_total=sum(j.score for j in jds),
                         feedback=Feedback(summary="ok", suggestions=[]),
                         elapsed_sec=0.1, run_info=RunInfo(tokens=100, calls=len(items)))


@pytest.fixture()
def batch_env(monkeypatch, tmp_path):
    import tools.batch_run as br
    monkeypatch.setattr(br, "run_grading", fake_grading)
    monkeypatch.setattr(br.P, "parse_file", lambda p: ("这是一份用于测试的报告正文，长度足够。", []))
    return br


def _write_meta(tmp_path):
    d = tmp_path / "data" / "samples"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps([{"report_id": "S01", "original": "a.pdf", "chars": 1000}],
                   ensure_ascii=False), encoding="utf-8")


def test_batch_run_writes_versioned_file(batch_env, tmp_path, capsys):
    """batch_run 必须产出版本化文件，且记录是否启用复核"""
    _write_meta(tmp_path)
    batch_env.__dict__["ROOT"] = str(tmp_path)          # 把输出目录指到临时目录
    sys.argv = ["batch_run.py"]

    batch_env.main()
    outs = glob.glob(os.path.join(str(tmp_path), "data", "results", "batch_v2_*.json"))
    assert outs, "没有生成版本化结果文件"
    data = json.load(open(outs[0], encoding="utf-8"))
    assert data["version"] == "v2"
    assert data["config"]["enable_recheck"] is True       # 默认必须跑完整流水线
    assert data["config"]["rubric_type"] == "fixed_5item"
    assert len(data["results"]) == 1
    assert data["results"][0]["run_info"]["tokens"] == 100


def test_batch_run_records_when_recheck_disabled(batch_env, tmp_path):
    _write_meta(tmp_path)
    batch_env.__dict__["ROOT"] = str(tmp_path)
    sys.argv = ["batch_run.py", "--no-recheck"]
    batch_env.main()
    latest = os.path.join(str(tmp_path), "data", "results", "batch_latest.json")
    data = json.load(open(latest, encoding="utf-8"))
    assert data["config"]["enable_recheck"] is False, "关闭复核必须在结果里写明，不能假装跑过"


def test_benchmark_reads_latest_and_writes_versioned(tmp_path):
    """benchmark 读 batch_latest，输出版本化文件，并记录上下文"""
    import tools.benchmark as bm
    gold = {"created_at": "2026-09-22", "scorer": "测试评分人",
            "independent": "是", "no_ai_reference": "是",
            "reports": {"S01": {"total": 80, "items": {
                "r1": {"score": 60, "verdict": "hit"},
                "r2": {"score": 20, "verdict": "hit"},
                "r3": {"score": 0, "verdict": "miss"},
                "r4": {"score": 0, "verdict": "miss"},
                "r5": {"score": 0, "verdict": "miss"}}}}}
    batch = {"version": "v2", "config": {"rubric_type": "fixed_5item",
                                         "enable_recheck": True, "model": "m"},
             "results": [{"report_id": "S01", "total": 100, "ai_total": 100,
                          "chars": 1000, "elapsed_sec": 1.0,
                          "run_info": {"tokens": 100},
                          "details": [{"id": "r1", "v": "hit", "s": 15,
                                       "confidence": 0.9, "needs_review": False,
                                       "system_error": "", "evidence": [],
                                       "dropped": []}]}]}
    results_dir = tmp_path / "data" / "results"
    results_dir.mkdir(parents=True)
    (tmp_path / "data" / "gold").mkdir(parents=True)
    (tmp_path / "data" / "gold" / "gold.json").write_text(
        json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    bpath = results_dir / "batch_latest.json"
    bpath.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")

    bm.ROOT = str(tmp_path)
    bm.GOLD = str(tmp_path / "data" / "gold" / "gold.json")
    sys.argv = ["benchmark.py"]
    bm.main()

    outs = glob.glob(os.path.join(str(tmp_path), "data", "results", "benchmark_v2_*.json"))
    assert outs, "没有生成版本化 benchmark"
    data = json.load(open(outs[0], encoding="utf-8"))
    assert data["dataset"]["blind"] is True
    assert data["config"]["enable_recheck"] is True
    assert "mae" in data["metrics"] and "pearson" in data["metrics"]
    assert "review_rate" in data["metrics"] and "system_error_rate" in data["metrics"]
