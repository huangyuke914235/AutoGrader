# -*- coding: utf-8 -*-
"""模型参数差异 —— Kimi k2.x / k3 系列不接受自定义 temperature

背景（真实故障，2026-09-24）：
用户报告「换成 Kimi 通道就跑不动，用 DeepSeek 正常」。查 Kimi 官方《模型参数参考》
（platform.kimi.com/docs/api/models-overview）才知道：

    kimi-k2.6 / kimi-k2.7-code / kimi-k3 的 temperature、top_p、n 均为**固定值**
    （思考模式 temperature=1.0，非思考 0.6；top_p 固定 0.95），
    「传入其他值会报错」。

而我们这条流水线为了评分可复现，一贯传 temperature=0.0。DeepSeek 接受 0，
Kimi 直接 400 invalid_request_error —— 现象完全一致的两条通道，一个通一个挂，
从界面提示上根本看不出差别。

这组测试要钉住的就一件事：**按模型名决定这个参数发还是不发**。
"""
from types import SimpleNamespace

import pytest

import llm
from models import SelfCheckPayload


# ---------------- 测试替身 ----------------

class _Resp:
    def __init__(self, content):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(total_tokens=1)


class _ModelList:
    def __init__(self, ids):
        self.data = [SimpleNamespace(id=i) for i in ids]


def _recording_client(payload='{"items": []}', exc=None, models=None,
                      model_exc=None):
    """建一个会记录**每次真实 kwargs** 的假客户端。

    为什么非盯 kwarg 不可：这一类 bug 的共同特征是「调用成功、参数缺席」。
    只看「没抛异常」验证不了任何东西 —— 传错模型和传对模型都会成功返回。
    必须看它到底把什么发了出去（呼应项目纪律：'配置合法' ≠ '配置真被用上'）。
    """
    calls, ctor = [], {}

    class _Completions:
        def create(self, **kw):
            calls.append(kw)
            if exc:
                raise exc
            return _Resp(payload)

    class _Models:
        def list(self):
            if model_exc:
                raise model_exc
            if models is None:
                raise RuntimeError("no models endpoint")
            return _ModelList(models)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()
        models = _Models()

    return _Client(), calls, ctor


# ---------------- 1. 参数表本身 ----------------

@pytest.mark.parametrize("model,allowed", [
    ("kimi-k2.6", False),        # 思考 1.0 / 非思考 0.6，传别的都报错
    ("kimi-k2.7-code", False),
    ("kimi-k3", False),
    ("deepseek-chat", True),     # 各厂商通行实现，接受自定义值
    ("qwen-plus", True),
    ("glm-4-flash", True),
    ("gpt-4o-mini", True),
    ("", True),                  # 空模型名不能把人挡在门外，交给后端报错
])
def test_temperature_allowed_table(model, allowed):
    assert llm.temperature_allowed(model) is allowed


def test_temperature_allowed_is_case_insensitive():
    """学生从控制台复制模型名时大小写不定，不能因为大写就没了这条保护。"""
    assert llm.temperature_allowed("KIMI-K2.6") is False


# ---------------- 2. 参数是否真的发出去 ----------------

def test_kimi_call_omits_temperature_entirely(monkeypatch):
    """核心断言：Kimi 系列必须**整个参数都不传**，而不是传 0、也不是传 None。"""
    client, calls, _ = _recording_client()
    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: client)

    llm.call_json("sys", "user", SelfCheckPayload, temperature=0.0,
                  api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                  model="kimi-k2.6")

    assert len(calls) == 1
    assert "temperature" not in calls[0], (
        "Kimi k2.6 的 temperature 是平台固定值，传任何值（含 0）都会 400。"
        "必须是「不传」，不是「传默认值」")
    # 其他正常参数不能跟着一起消失
    assert calls[0]["model"] == "kimi-k2.6"
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_deepseek_keeps_zero_temperature(monkeypatch):
    """反向锚定：这条适配只能影响 Kimi，不能顺手把别家的可复现性弄丢。"""
    client, calls, _ = _recording_client()
    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: client)

    llm.call_json("sys", "user", SelfCheckPayload, temperature=0.0,
                  api_key="sk-x", base_url="https://api.deepseek.com/v1",
                  model="deepseek-chat")

    assert calls[0].get("temperature") == 0.0, (
        "DeepSeek 侧仍要锁 temperature=0 保证评分可复现")


def test_pipeline_recheck_temperature_is_forwarded_when_supported(monkeypatch):
    """pipeline 的重试判定传 0.3：允许的厂商要照原样发出去。"""
    client, calls, _ = _recording_client()
    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: client)

    llm.call_json("sys", "user", SelfCheckPayload, temperature=0.3,
                  api_key="sk-x", base_url="https://api.deepseek.com/v1",
                  model="deepseek-chat")

    assert calls[0].get("temperature") == pytest.approx(0.3)


def test_json_mode_fallback_also_respects_temperature_rule(monkeypatch):
    """降级分支（不支持 response_format）同样得遵守温度规则，别留个后门。"""
    class NoJsonMode(TypeError):
        pass

    class Compl:
        def __init__(self, caller):
            self.calls = caller

        def create(self, **kw):
            self.calls.append(kw)
            if "response_format" in kw:
                raise NoJsonMode("unexpected keyword argument 'response_format'")
            return _Resp('{"items": []}')

    calls = []

    class Chat:
        completions = Compl(calls)

    class Client:
        chat = Chat()

    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: Client())
    llm.call_json("sys", "user", SelfCheckPayload, temperature=0.0,
                  api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                  model="kimi-k2.6")

    assert len(calls) == 2, "第一次因 response_format 失败，应降级重试一次"
    assert "temperature" not in calls[1], "降级路径也必须剔除 temperature"


# ---------------- 3. 诊断函数 ----------------

def test_diagnose_succeeds_against_a_healthy_endpoint(monkeypatch):
    """健康通道要走到第 ④ 步并把 ok 置 True。"""
    client, calls, _ = _recording_client(models=["kimi-k2.6", "kimi-k3"])
    monkeypatch.setattr("openai.OpenAI", lambda **kw: client)

    d = llm.diagnose(api_key="sk-secret-key-1234",
                     base_url="https://api.moonshot.cn/v1", model="kimi-k2.6")

    assert d["ok"] is True
    assert len(d["steps"]) == 4
    assert all(ok for _, ok, _ in d["steps"]), [s for s in d["steps"]]
    assert d["models"] == ["kimi-k2.6", "kimi-k3"]
    # 诊断自己也要守规矩：对 Kimi 不能传 temperature
    assert "temperature" not in calls[0]


def test_diagnose_never_leaks_the_api_key(monkeypatch):
    """诊断结果会被整段复制粘贴出去求助 —— 密钥一个字符都不能跟着出去。"""
    class Boom(Exception):
        status_code = 401

    client, _, _ = _recording_client(model_exc=Boom(
        "Error code: 401 - Invalid Authorization, your key is sk-secret-key-1234"))
    monkeypatch.setattr("openai.OpenAI", lambda **kw: client)

    d = llm.diagnose(api_key="sk-secret-key-1234",
                     base_url="https://api.moonshot.cn/v1", model="kimi-k2.6")

    blob = str(d)
    assert "1234" not in blob, f"诊断结果泄露了密钥：{blob}"
    assert "secret" not in blob, f"诊断结果泄露了密钥中段：{blob}"
    assert "***" in blob, "密钥应当被脱敏而不是整段丢掉"


def test_diagnose_redacts_partial_key_echoed_by_the_platform(monkeypatch):
    """形态兜底：有些平台会把 key 的一部分回显在报错里，精确替换够不着。"""
    class Boom(Exception):
        status_code = 401

    client, _, _ = _recording_client(model_exc=Boom(
        "401 Invalid Authorization: key sk-Zk3jT9mQpLxv is not valid"))
    monkeypatch.setattr("openai.OpenAI", lambda **kw: client)

    d = llm.diagnose(api_key="sk-0000000000",
                     base_url="https://api.moonshot.cn/v1", model="kimi-k2.6")

    assert "Zk3jT9mQpLxv" not in str(d), f"平台回显的密钥片段没被脱敏：{d}"


def test_diagnose_auth_failure_points_at_the_two_platforms(monkeypatch):
    """Kimi 国内站与国际站的 Key 互不通用 —— 这是 401 最常见也最难想到的一条。"""
    class Boom(Exception):
        status_code = 401

    client, _, _ = _recording_client(model_exc=Boom("401 Invalid Authorization"))
    monkeypatch.setattr("openai.OpenAI", lambda **kw: client)

    d = llm.diagnose(api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                     model="kimi-k2.6")

    assert d["ok"] is False
    assert len(d["steps"]) == 1, "认证层没过就不必继续往下试"
    assert "api.moonshot.ai" in d["hint"], "必须点出另一个可能的正确端点"


def test_diagnose_flags_model_not_in_list(monkeypatch):
    """账号名下没有这个模型（换账号 / 未开通）要比鬼打墙 404 说得明白。"""
    client, _, _ = _recording_client(models=["kimi-k2.6"],
                                     payload='{"a": 1}')
    monkeypatch.setattr("openai.OpenAI", lambda **kw: client)

    d = llm.diagnose(api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                     model="moonshot-v1-8k")

    ok_list = [ok for _, ok, _ in d["steps"]]
    assert ok_list[1] is False, "第 ② 步（检查模型名）必须判失败"
    assert "kimi-k2.6" in d["hint"], "要给出具体可替换的型号"


def test_diagnose_detects_temperature_rejection(monkeypatch):
    """③ 不通且报错指向 temperature 时，结论要直接落到采样参数上。

    这一条是专门针对本次 Kimi 故障的：症状相同（跑不动），但只有把
    报错原文和分诊结论对上，才能不再聊第二轮。
    """
    class BadTemp(Exception):
        status_code = 400

    client, calls, _ = _recording_client(
        models=["moonshot-v1-128k"],
        exc=BadTemp("invalid_request_error: temperature must be 1.0 for thinking mode"))
    monkeypatch.setattr("openai.OpenAI", lambda **kw: client)

    d = llm.diagnose(api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                     model="moonshot-v1-128k")

    assert d["ok"] is False
    third_ok = d["steps"][-1][1]
    assert third_ok is False
    assert "temperature" in d["hint"], f"分诊结论应指向采样参数，实际：{d['hint']}"


# ---------------- 关掉「思考模式」：必须精确到型号，不能按前缀一刀切 ----------------
#
# Kimi k2.6 默认开着思考模式（先写一大段推理再作答），单次调用慢好几倍。
# 能不能关，取决于**具体型号**（与 temperature 那条是同一个坑的镜像）：
#   kimi-k2.6      支持 disabled ✔ 可以关
#   kimi-k2.7-code 只接受 enabled，传 disabled 会被拒 ✘
#   kimi-k3        官方参数表里这一行是「—」，压根没这个参数 ✘
# 图省事按 "kimi-" 前缀一刀切，把 k2.7 / k3 也带上 disabled，
# 就是把「慢」治成了「跑不起来」——比不优化更糟。


@pytest.mark.parametrize("model,expect_off", [
    ("kimi-k2.6", True),
    ("kimi-k2.7-code", False),
    ("kimi-k3", False),
    ("deepseek-chat", False),
])
def test_thinking_is_switched_off_only_where_the_model_allows_it(
        monkeypatch, model, expect_off):
    """只有明确支持 disabled 的型号才发这个参数，其余一个字都别加。"""
    client, calls, _ = _recording_client()
    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: client)

    llm.call_json("sys", "user", SelfCheckPayload, temperature=0.0,
                  api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                  model=model)

    sent = calls[-1].get("extra_body", {})
    if expect_off:
        assert sent.get("thinking") == {"type": "disabled"}, \
            f"{model} 支持关闭思考模式，就该真的发出去：{calls[-1]}"
    else:
        assert "thinking" not in sent, \
            f"{model} 不接受这个参数，发了就是 400：{calls[-1]}"


def test_fast_flag_false_keeps_the_platform_default(monkeypatch):
    """fast=False 时交还平台默认值 —— 是「不传」，不是「传 enabled」。"""
    client, calls, _ = _recording_client()
    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: client)

    llm.call_json("sys", "user", SelfCheckPayload, temperature=0.0,
                  api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                  model="kimi-k2.6", fast=False)

    assert "extra_body" not in calls[-1], \
        f"fast=False 就不该带任何私有参数：{calls[-1]}"
