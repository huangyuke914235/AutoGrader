# -*- coding: utf-8 -*-
"""模型通道贯通测试：侧边栏填的 key，到底有没有真的传到调用层？

现场现象（2026-09-24，用户截图）：
    侧边栏选了「DeepSeek 深度求索」、key 也填了、界面绿字「已就绪」，
    一点「② 开始评阅」却报
    「RuntimeError: DEMO_MODE=true：已阻止调用真实模型。」

根因：pipeline 里 R/A/A-重试/G/E 五处 call_json 全部不传 api_key，
于是 llm.call_json 里那句「学生显式自带的密钥应当放行」永远不成立
（它看到的永远是 api_key=None），接着撞上 DEMO_MODE 的兜底拦截。

这组测试锁住修复后的行为。**第 3、4 条是核心回归**：
只要学生显式自带密钥，DEMO_MODE 不得再拦。
"""
import os

import pytest

import pipeline
import providers
from models import Rubric, RubricItem, ItemJudgement, Evidence, Feedback, GradingResult

FULL = "本报告写清了实验目的，并给出了完整的实现过程与运行结果数据，最后做了分析总结。"
ITEM = RubricItem(id="r1", name="实现", criteria="是否有实现过程", max_score=20)
GOOD = "完整的实现过程"

DS_KEY = "sk-abcdefghijklmnopqrst"


def ds_cfg():
    """学生自己在侧边栏选好、填好 key 之后的配置"""
    return providers.build_cfg("deepseek", DS_KEY,
                               "https://api.deepseek.com/v1", "deepseek-chat")


@pytest.fixture()
def spy(monkeypatch):
    """记录 call_json 每次收到的显式参数，用来证明密钥真的透传到了调用层"""
    seen = []

    def _f(system, user, schema, temperature=0.0, **kw):
        seen.append(kw)
        if schema is ItemJudgement:
            return ItemJudgement(rubric_item_id="r1", verdict="hit", score=18,
                                 confidence=0.9, reason="理由",
                                 evidence=[Evidence(quote=GOOD)])
        raise AssertionError(f"本测试只关心 A 阶段，收到 {schema}")

    monkeypatch.setattr(pipeline, "call_json", _f)
    # E 阶段单独打桩：它不参与本组断言，真调会拖慢测试
    monkeypatch.setattr(pipeline, "stage_feedback",
                        lambda items, judgements, llm_cfg=None:
                        Feedback(summary="ok", suggestions=[]))
    return seen


# ---------- llm_kwargs：唯一一处「配置 → 调用参数」的翻译 ----------

def test_no_cfg_and_offline_produce_no_explicit_key():
    """None（调用方没指定）与 offline（纯规则）都不该显式传密钥"""
    assert providers.llm_kwargs(None) == {}
    assert providers.llm_kwargs(providers.build_cfg("offline")) == {}


def test_incomplete_cfg_produces_no_explicit_key():
    """配置不全时宁可不传，也不要塞半成品密钥进去"""
    assert providers.llm_kwargs({"id": "deepseek", "api_key": "",
                                 "base_url": "", "model": ""}) == {}


def test_cfg_carries_three_fields_verbatim():
    assert providers.llm_kwargs(ds_cfg()) == {
        "api_key": DS_KEY,
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    }


# ---------- 核心回归 ----------

def test_student_key_reaches_every_stage(spy, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    res = pipeline.run_grading(FULL, [], "原始标准", rubric=Rubric(items=[ITEM]),
                               enable_recheck=False, llm_cfg=ds_cfg())
    assert spy, "call_json 一次都没被调用 —— 密钥显然没传下去"
    assert all(kw.get("api_key") == DS_KEY for kw in spy)
    assert res.judgements[0].score == 18


def test_demo_mode_does_not_block_student_key(spy, monkeypatch):
    """核心回归：DEMO_MODE=true + 学生自带 key → 必须放行（曾经正是在这里报错）"""
    monkeypatch.setenv("DEMO_MODE", "true")
    pipeline.DEMO_RESULT = ("预置演示结果",)
    try:
        res = pipeline.run_grading(FULL, [], "原始标准", rubric=Rubric(items=[ITEM]),
                                   enable_recheck=False, llm_cfg=ds_cfg())
        assert isinstance(res, GradingResult), "返回了预置结果，说明还是被 DEMO_MODE 拦了"
        assert spy, "没有真的走到模型调用"
    finally:
        pipeline.DEMO_RESULT = None


# ---------- DEMO_MODE 原有的拦截力必须原样保留 ----------
#
# 2026-10 变更说明：DEMO_MODE 下没有显式密钥时，现在会先尝试加载**仓库内置的预置
# 评阅结果**（`data/demo/*.json`）。这是为了让公开 Demo 不填 Key 也能看到完整产品，
# 而旧实现只有 DEMO_RESULT 一个来源、且它恒为 None，于是点「开始评阅」必然抛异常。
#
# 但底线不能动：**绝不因为 DEMO_MODE 就偷偷用平台配置去调模型**（那是烧钱事故）。
# 所以下面两条必须同时成立：
#   ① 有预置结果 → 返回它，且一次模型调用都不发生；
#   ② 没有预置结果 → 抛异常，同样一次模型调用都不发生。

def test_demo_mode_never_calls_model_even_with_platform_key(tmp_path, monkeypatch):
    """**核心安全测试**：DEMO_MODE + 平台密钥在环境里，也绝不允许真的调用模型。"""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_API_KEY", "sk-platform-key-should-never-be-used")
    monkeypatch.setattr(pipeline, "ROOT", str(tmp_path))   # 隔离：没有预置结果
    pipeline.DEMO_RESULT = None
    called = []

    def spy(*a, **kw):
        called.append(kw)
        raise AssertionError("DEMO_MODE 下发生了真实模型调用 —— 这是烧钱事故")

    monkeypatch.setattr(pipeline, "call_json", spy)
    try:
        with pytest.raises(RuntimeError) as ei:
            pipeline.run_grading(FULL, [], "原始标准", rubric=Rubric(items=[ITEM]),
                                 enable_recheck=False, llm_cfg=None)
        assert "DEMO_MODE" in str(ei.value)
        assert not called, "拦截失败：模型被真的调用了"
    finally:
        pipeline.DEMO_RESULT = None


def test_demo_mode_serves_preset_result_without_calling_model(tmp_path, monkeypatch):
    """有预置结果时返回它 —— 这是公开 Demo「零配置可看」的实现方式。"""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("LLM_API_KEY", "sk-platform-key-should-never-be-used")
    monkeypatch.setattr(pipeline, "ROOT", str(tmp_path))
    called = []
    monkeypatch.setattr(pipeline, "call_json",
                        lambda *a, **kw: called.append(kw) or (_ for _ in ()).throw(
                            AssertionError("不该调用模型")))
    pipeline.DEMO_RESULT = ("PRE",)
    try:
        got = pipeline.run_grading(FULL, [], "原始标准",
                                   rubric=Rubric(items=[ITEM]), llm_cfg=None)
        assert got == ("PRE",)
        assert not called
    finally:
        pipeline.DEMO_RESULT = None


def test_demo_mode_loads_preset_from_disk_when_memory_empty(tmp_path, monkeypatch):
    """内存里没有、但磁盘上有预置结果时，也必须能返回（旧实现的缺口就在这里）"""
    import json as _json
    from models import GradingResult
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "ROOT", str(tmp_path))
    d = tmp_path / "data" / "demo"
    os.makedirs(d, exist_ok=True)
    res = pipeline.run_offline_grading(FULL, [], pipeline.default_rubric().items,
                                       report_id="PRESET")
    (d / "T1.json").write_text(_json.dumps(
        {"_meta": {"engine": "rule"}, "result": _json.loads(res.model_dump_json())},
        ensure_ascii=False), encoding="utf-8")
    pipeline.DEMO_RESULT = None
    try:
        got = pipeline.run_grading(FULL, [], "原始标准",
                                   rubric=Rubric(items=[ITEM]), llm_cfg=None)
        assert isinstance(got, GradingResult)
        assert got.report_id == "PRESET"
    finally:
        pipeline.DEMO_RESULT = None


def test_no_cfg_keeps_legacy_env_behaviour(spy, monkeypatch):
    """llm_cfg=None（CLI / 批量脚本）仍走环境变量，老行为不能变"""
    monkeypatch.setenv("DEMO_MODE", "false")
    pipeline.run_grading(FULL, [], "原始标准", rubric=Rubric(items=[ITEM]),
                         enable_recheck=False)
    assert spy and all(kw == {} for kw in spy)


# ---------- 两类「选错通道」的报错必须可执行 ----------

def test_offline_channel_refuses_grading_and_says_what_to_do():
    """离线通道跑评阅要当场拒绝，并告诉用户去哪改 —— 不能静默退化"""
    with pytest.raises(RuntimeError) as ei:
        pipeline.run_grading(FULL, [], "原始标准", rubric=Rubric(items=[ITEM]),
                             llm_cfg=providers.build_cfg("offline"))
    msg = str(ei.value)
    assert "学生自检" in msg, "报错必须指出「只想做规则体检就去学生自检」"


def test_run_info_records_the_channel_actually_used(spy, monkeypatch):
    """导出里的模型名必须是**本次真用**的那个，不能永远是环境变量那套"""
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("LLM_MODEL", "平台默认模型")
    monkeypatch.setenv("LLM_BASE_URL", "https://platform.example/v1")
    res = pipeline.run_grading(FULL, [], "原始标准", rubric=Rubric(items=[ITEM]),
                               enable_recheck=False, llm_cfg=ds_cfg())
    assert res.model == "deepseek-chat"
    assert res.run_info.base_url == "https://api.deepseek.com/v1"
