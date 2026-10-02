# -*- coding: utf-8 -*-
"""零配置演示链路 + 离线规则引擎的行为测试

这两件事是同一个目标的两面：**公开 Demo 上不填 API Key 也要能看到完整产品**。
它们也是最容易被后续改动悄悄破坏的地方 —— 例如有人把 DEMO_MODE 分支改回去，
或者规则引擎给出的证据不再能从原文里逐字找到。所以必须有测试守着。
"""
import json
import os

import pytest

import llm
import parser as P
import pipeline as PL
from models import GradingResult, RubricItem


SAMPLE = """实验报告：Java 语言应用

实验目的
本次实验旨在掌握 JDK 环境配置与基本语法，了解面向对象的基本概念。

实验环境与步骤
安装 JDK 25，配置 JAVA_HOME 与 Path，使用 IntelliJ IDEA 创建项目。
命令行执行 java -version 与 javac -version 验证版本一致。

核心实现
编写 BankAccount 类，通过构造方法初始化账户，deposit 与 withdraw 方法实现资金变动，
withdraw 保证余额不为负，toString 统一输出账户信息。

运行结果
程序输出：余额不足，取款失败。
截图见下（控制台输出）。

分析与总结
整体时间复杂度为 O(n)，空间复杂度为 O(1)。通过本次实验掌握了封装与继承的区别，
结论是封装降低了模块间耦合度。
"""


# ---------- 离线规则引擎 ----------

def test_rule_judge_evidence_is_verbatim_in_source():
    """规则引擎给的每条证据都必须能在原文里逐字找到 —— 这是它能当演示的前提"""
    items = PL.default_rubric().items
    for it in items:
        j = PL.rule_judge(it, P.split_sections(SAMPLE), SAMPLE)
        for e in j.evidence:
            assert e.quote in SAMPLE, f"{it.id} 的证据不在原文里：{e.quote!r}"
            assert e.char_start >= 0


def test_rule_judge_always_flags_manual_review():
    """规则通道一律标注待人工复核，绝不冒充终评"""
    items = PL.default_rubric().items
    for it in items:
        j = PL.rule_judge(it, P.split_sections(SAMPLE), SAMPLE)
        assert j.needs_review is True
        assert "规则" in j.reason


def test_rule_judge_misses_when_nothing_matches():
    """正文里完全没有相关线索时必须判 miss，且不带证据（不能靠编造凑分）"""
    item = RubricItem(id="rx", name="误差分析", criteria="对误差来源做定量分析",
                      max_score=10, positive_signals=["误差", "标准差", "置信区间"])
    j = PL.rule_judge(item, P.split_sections(SAMPLE), SAMPLE)
    assert j.verdict == "miss"
    assert j.score == 0.0
    assert j.evidence == []


def test_rule_judge_caps_score_at_max():
    items = PL.default_rubric().items
    judgements = [PL.rule_judge(it, [], SAMPLE) for it in items]
    for it, j in zip(items, judgements):
        assert 0 <= j.score <= it.max_score + 1e-9


def test_run_offline_grading_total_is_sum_of_items():
    """铁律一在离线通道同样成立：总分由代码加总，不接受任何别的来源"""
    items = PL.default_rubric().items
    res = PL.run_offline_grading(SAMPLE, P.split_sections(SAMPLE), items,
                                 report_id="T1")
    assert abs(res.total - round(sum(j.score for j in res.judgements), 1)) < 0.05
    assert res.ai_total == res.total
    assert res.model == "offline-rule-engine"
    assert res.total_incomplete is False


def test_run_offline_grading_produces_usable_feedback():
    items = PL.default_rubric().items
    res = PL.run_offline_grading(SAMPLE, P.split_sections(SAMPLE), items)
    assert res.feedback is not None
    assert res.feedback.summary
    assert res.feedback.suggestions
    # 评语必须如实说明它不是模型产物
    assert "规则" in res.feedback.summary


def test_offline_result_serializes_and_reloads():
    """预置演示要落盘再读回来，契约必须双向成立"""
    items = PL.default_rubric().items
    res = PL.run_offline_grading(SAMPLE, P.split_sections(SAMPLE), items, report_id="T2")
    again = GradingResult.model_validate(json.loads(res.model_dump_json()))
    assert again.report_id == "T2"
    assert len(again.judgements) == len(res.judgements)
    assert again.ocr is None and again.demo_meta is None


# ---------- 预置演示结果 ----------

def _write_demo(tmp_path, monkeypatch, rid="T1", engine="rule"):
    items = PL.default_rubric().items
    res = PL.run_offline_grading(SAMPLE, P.split_sections(SAMPLE), items, report_id=rid)
    out = tmp_path / "data" / "demo" / f"{rid}.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    monkeypatch.setattr(PL, "ROOT", str(tmp_path))
    PL.save_demo_result(str(out), res, {"engine": engine, "full_text": SAMPLE,
                                        "built_at": "2026-01-01T00:00:00"})
    return str(out)


def test_demo_candidates_and_load_roundtrip(tmp_path, monkeypatch):
    path = _write_demo(tmp_path, monkeypatch)
    assert [os.path.normpath(p) for p in PL.demo_candidates()] == [os.path.normpath(path)]

    res = PL.load_demo_result(path)
    assert res is not None
    assert res.report_id == "T1"
    # full_text 必须随预置结果一起带回来，详情页的原文高亮才有依据。
    # 回归点：曾经把整个 payload 当元信息，导致 demo_meta 里只有 "_meta"/"result"，
    # 载入演示后详情页永远空白 —— 而那一屏正是整个演示的重点。
    assert res.demo_meta["full_text"] == SAMPLE
    assert res.demo_meta["engine"] == "rule"


def test_load_demo_result_accepts_flat_legacy_payload(tmp_path):
    """兼容早期平铺格式（元信息与 result 同层），不能因为格式变化就读不出来"""
    items = PL.default_rubric().items
    res = PL.run_offline_grading(SAMPLE, P.split_sections(SAMPLE), items, report_id="T7")
    p = tmp_path / "flat.json"
    p.write_text(json.dumps({"engine": "rule", "full_text": SAMPLE,
                             "result": json.loads(res.model_dump_json())},
                            ensure_ascii=False), encoding="utf-8")
    got = PL.load_demo_result(str(p))
    assert got is not None
    assert got.demo_meta["engine"] == "rule"
    assert got.demo_meta["full_text"] == SAMPLE


def test_load_first_demo_result_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(PL, "ROOT", str(tmp_path))
    res, path = PL.load_first_demo_result()
    assert res is None and path == ""


def test_corrupt_demo_file_is_ignored_not_crashing(tmp_path, monkeypatch):
    """预置结果损坏时只能返回 None —— 公开 Demo 绝不能因为这个崩掉"""
    monkeypatch.setattr(PL, "ROOT", str(tmp_path))
    d = tmp_path / "data" / "demo"
    os.makedirs(d, exist_ok=True)
    (d / "broken.json").write_text("{ 这不是合法 JSON", encoding="utf-8")
    assert PL.load_demo_result(str(d / "broken.json")) is None
    res, path = PL.load_first_demo_result()
    assert res is None


def test_run_grading_returns_demo_result_without_api_key(tmp_path, monkeypatch):
    """核心回归：DEMO_MODE 下点「开始评阅」必须返回预置结果。

    旧实现只检查模块级的 DEMO_RESULT，而它全仓库没有任何一处赋值 ——
    于是公开 Demo 上这个按钮必然抛 RuntimeError，评委看到的是原始报错页。
    """
    path = _write_demo(tmp_path, monkeypatch, rid="T9")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(PL, "DEMO_RESULT", None)

    res = PL.run_grading(SAMPLE, P.split_sections(SAMPLE), "评分标准若干条",
                         report_id="T9", llm_cfg=None)
    assert res.report_id == "T9"
    assert res.model == "offline-rule-engine"


def test_run_grading_still_raises_when_no_demo_available(tmp_path, monkeypatch):
    """没有任何预置结果时，报错必须是人话（指明怎么生成），而不是静默失败"""
    monkeypatch.setattr(PL, "ROOT", str(tmp_path))
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(PL, "DEMO_RESULT", None)
    with pytest.raises(RuntimeError) as e:
        PL.run_grading(SAMPLE, P.split_sections(SAMPLE), "标准", report_id="T9")
    assert "build_demo" in str(e.value)


def test_explicit_api_key_bypasses_demo_mode(tmp_path, monkeypatch):
    """学生自带密钥属于显式意图，必须放行 —— DEMO_MODE 只防误烧平台额度

    判据：报错来自**真实链路**，而不是 DEMO_MODE 的预置结果分支。
    旧实现会因为「DEMO_MODE=true 且没配密钥」直接拦掉自带密钥的调用，
    这正是 2026-09-24 那个「填了 Key 反而报 DEMO_MODE」的现象。
    """
    monkeypatch.setattr(PL, "ROOT", str(tmp_path))       # 确保没有预置结果可退
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(PL, "DEMO_RESULT", None)

    def boom_rubric(*a, **kw):
        raise RuntimeError("REAL-PATH-REACHED")

    monkeypatch.setattr(PL, "stage_rubric", boom_rubric)
    with pytest.raises(RuntimeError) as e:
        PL.run_grading(SAMPLE, P.split_sections(SAMPLE), "标准",
                       llm_cfg={"id": "deepseek", "api_key": "sk-test",
                                "base_url": "https://api.deepseek.com",
                                "model": "deepseek-flash"})
    msg = str(e.value)
    assert "REAL-PATH-REACHED" in msg, msg
    assert "DEMO_MODE" not in msg, "自带密钥被 DEMO_MODE 拦下了，这是回归"


# ---------- OCR 统计接进 RunInfo ----------

def test_apply_ocr_to_runinfo_copies_stats():
    items = PL.default_rubric().items
    res = PL.run_offline_grading(SAMPLE, P.split_sections(SAMPLE), items)
    info = res.run_info
    PL.apply_ocr_to_runinfo(info, {"used": True, "model": "deepseek-flash",
                                   "pages": 7, "chars": 1234, "images": 5,
                                   "failed": 1, "cached_hits": 3})
    assert info.ocr_used is True
    assert info.ocr_model == "deepseek-flash"
    assert (info.ocr_pages, info.ocr_chars, info.ocr_images) == (7, 1234, 5)
    assert (info.ocr_failed, info.ocr_cached_hits) == (1, 3)


def test_ocr_disabled_reports_reason_instead_of_silence():
    """没开启 OCR 时要给出明确原因，界面才能如实显示，而不是让人以为读过了"""
    ft, secs, info = PL.run_ocr_enrichment("", SAMPLE, P.split_sections(SAMPLE),
                                           enabled=False)
    assert info["used"] is False
    assert info["skipped_reason"]
    assert ft == SAMPLE


def test_ocr_on_missing_file_is_not_fatal():
    ft, secs, info = PL.run_ocr_enrichment("/nonexistent/report.pdf", SAMPLE,
                                           P.split_sections(SAMPLE), enabled=True)
    assert info["used"] is False
    assert info["skipped_reason"]
    assert ft == SAMPLE          # 正文原样返回：OCR 缺失不能影响评阅
