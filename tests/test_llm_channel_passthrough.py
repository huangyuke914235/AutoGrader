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

def test_demo_mode_still_blocks_platform_config(monkeypatch):
    """没有显式密钥时（可能烧平台的钱）拦截照旧"""
    monkeypatch.setenv("DEMO_MODE", "true")
    pipeline.DEMO_RESULT = None
    with pytest.raises(RuntimeError) as ei:
        pipeline.run_grading(FULL, [], "原始标准", rubric=Rubric(items=[ITEM]),
                             enable_recheck=False, llm_cfg=None)
    assert "DEMO_MODE" in str(ei.value)


def test_demo_mode_returns_preset_result_without_key(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    pipeline.DEMO_RESULT = ("PRE",)
    try:
        got = pipeline.run_grading(FULL, [], "原始标准",
                                   rubric=Rubric(items=[ITEM]), llm_cfg=None)
        assert got == ("PRE",)
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
