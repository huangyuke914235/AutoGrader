# -*- coding: utf-8 -*-
"""模型接入（自选 API）的回归测试

重点锁三条纪律：
1. 通道配置**只沿调用链传递**，绝不写入模块级全局（Streamlit 多会话共享模块）
2. 配置不完整 / 离线通道 → 4 项判「未检测」，绝不当 0 分，且要给出可操作的原因
3. 密钥在界面回显、导出摘要、异常消息里一律脱敏
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers  # noqa: E402
import selfcheck as SC  # noqa: E402

SECRET = "sk-secretkey-1234"


def test_offline_channel_marks_ai_items_undetected():
    items = SC.ai_check("实验目的：验证欧姆定律。", [], llm_cfg=providers.build_cfg("offline"))
    assert len(items) == len(SC.AI_ITEMS) == 4
    assert all(i.status == "未检测" for i in items)
    assert all(i.score == 0 for i in items)
    # 必须告诉学生怎么补救，而不是一句干巴巴的「未检测」
    assert any("离线" in (x.problem or "") for x in items[0].issues)


def test_incomplete_config_reports_reason_without_calling(monkeypatch):
    """配置不全就别发起调用 —— 否则学生要等 60 秒超时才看到一句看不懂的报错。"""
    called = []

    def boom(*_a, **_kw):
        called.append(1)
        raise AssertionError("不该发起调用")

    monkeypatch.setattr(SC, "call_json", boom)
    cfg = providers.build_cfg("deepseek", api_key="")
    assert "API Key" in providers.validate(cfg)
    items = SC.ai_check("正文", [], llm_cfg=cfg)
    assert called == []
    assert all(i.status == "未检测" for i in items)
    assert "缺少" in items[0].issues[0].problem


def test_api_key_is_passed_explicitly_not_stored_globally(monkeypatch):
    """核心安全测试：密钥必须显式传到 call_json，不能靠模块级变量。

    若哪天有人改成「界面写个全局、调用方去读」，Streamlit 下 A 同学的 key
    就会被 B 同学的会话用掉 —— 既是安全事故也是计费事故。
    """
    captured = {}

    def fake_call(_system, _user, _schema, **kw):
        captured.update(kw)
        raise RuntimeError("stop here")

    monkeypatch.setattr(SC, "call_json", fake_call)
    cfg = providers.build_cfg("deepseek", SECRET)
    SC.ai_check("正文", [], llm_cfg=cfg)
    assert captured.get("api_key") == SECRET
    assert captured.get("base_url") == "https://api.deepseek.com/v1"
    assert captured.get("model") == "deepseek-chat"


def test_two_different_keys_do_not_leak_between_calls(monkeypatch):
    """同一进程里先后用两个不同密钥调用，第二次绝不能看到第一次的。"""
    seen = []

    def fake_call(_s, _u, _schema, **kw):
        seen.append(kw.get("api_key"))
        raise RuntimeError("stop")

    monkeypatch.setattr(SC, "call_json", fake_call)
    SC.ai_check("正文", [], llm_cfg=providers.build_cfg("deepseek", "sk-AAAA-1111"))
    SC.ai_check("正文", [], llm_cfg=providers.build_cfg("qwen", "sk-BBBB-2222"))
    assert seen == ["sk-AAAA-1111", "sk-BBBB-2222"]


def test_error_message_never_leaks_key(monkeypatch):
    """供应商的网络异常有时会带着完整请求 URL（含 key）回显。"""

    def fake_call(*_a, **_kw):
        raise RuntimeError(f"401 Unauthorized: https://api.deepseek.com/v1?key={SECRET}")

    monkeypatch.setattr(SC, "call_json", fake_call)
    items = SC.ai_check("正文", [], llm_cfg=providers.build_cfg("deepseek", SECRET))
    blob = " ".join((x.problem or "") for i in items for x in i.issues)
    assert SECRET not in blob, "异常消息里泄漏了密钥"
    assert providers.mask_key(SECRET) in blob, "应当显示脱敏后的密钥"


def test_mask_key_hides_middle():
    m = providers.mask_key("sk-abcdefgh1234")
    assert m.endswith("1234")
    assert "abcdefgh" not in m
    assert providers.mask_key("") == ""


def test_describe_is_safe_for_export():
    """导出文件头会带上通道摘要，里面不能有原文密钥。"""
    cfg = providers.build_cfg("deepseek", SECRET)
    d = providers.describe(cfg)
    assert SECRET not in d
    assert "deepseek-chat" in d


def test_shared_channel_off_by_default(monkeypatch):
    """平台共享额度默认关闭 —— 一个公开链接不该能刷爆账单。"""
    monkeypatch.delenv("ENABLE_SHARED_AI", raising=False)
    monkeypatch.delenv("SHARED_LLM_API_KEY", raising=False)
    assert providers.shared_available() is False
    assert providers.shared_cfg() is None


def test_shared_channel_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("SHARED_LLM_API_KEY", "sk-shared-9999")
    monkeypatch.delenv("ENABLE_SHARED_AI", raising=False)
    assert providers.shared_available() is False, "光配 key 还不够，必须显式开关"


def test_validate_accepts_http_and_https_only():
    ok = providers.build_cfg("custom", "sk-1", "https://x/v1", "m")
    assert providers.validate(ok) == ""
    bad = providers.build_cfg("custom", "sk-1", "x/v1", "m")
    assert "http" in providers.validate(bad)


def test_offline_always_valid():
    assert providers.validate(providers.build_cfg("offline")) == ""


def test_every_provider_has_required_fields():
    """预设供应商必须自带 base_url / model（学生填个 key 就能用）。
    offline 与 custom 例外：前者不用模型，后者本来就该由用户自己填。
    """
    for p in providers.PROVIDERS:
        assert p["id"] and p["name"] and p["note"]
        assert isinstance(p["needs_key"], bool)
        if p["id"] in ("offline", "custom"):
            continue
        assert p["base_url"] and p["model"], f"{p['id']} 缺 base_url/model"
        if p["needs_key"]:
            assert p["key_url"], f"{p['id']} 要填 key 却没给申请地址"


def test_preset_models_are_not_dead():
    """预设模型绝不能是各厂已退役的型号。

    教训（2026-09-24 用户实测）：Kimi 的 moonshot-v1 全系 2026-08-31 停服，
    预设还写着 moonshot-v1-8k，导致选 Kimi 必挂、选 DeepSeek 正常。
    """
    for p in providers.PROVIDERS:
        assert p.get("model", "") not in providers.DEAD_MODELS, \
            f"{p['id']} 预设的模型 {p['model']} 已退役，请更新预设"


def test_validate_flags_dead_model_with_replacement():
    """用户手填了已退役的模型名（旧教程抄来的）：要拦下并给出替代型号。"""
    cfg = providers.build_cfg("moonshot", "sk-x", model="moonshot-v1-8k")
    why = providers.validate(cfg)
    assert "已退役" in why
    assert "kimi-k2.6" in why, "必须给出可执行的替代型号，而不是只说「不行」"


def test_call_json_4xx_fails_fast_with_human_message(monkeypatch):
    """404（模型不存在/已退役）不该退避重试 3 次干等 10 秒 —— 立刻报人话。"""
    import llm
    from models import SelfCheckPayload

    class Fake404(Exception):
        status_code = 404

    class _Completions:
        def create(self, **_kw):
            raise Fake404("Error code: 404 - {'error': {'message': 'model not found'}}")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: _Client())
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))

    try:
        llm.call_json("sys", "user", SelfCheckPayload,
                      api_key="sk-x", base_url="https://api.moonshot.cn/v1",
                      model="moonshot-v1-8k")
        raise AssertionError("应当抛出 RuntimeError")
    except RuntimeError as e:
        msg = str(e)
        assert "404" in msg
        assert "moonshot-v1-8k" in msg, "报错里要点名是哪个模型出了问题"
        assert "在售" in msg or "退役" in msg, "要给出可执行的出路"
    assert slept == [], "4xx 是请求本身有问题，退避重试没意义"


def test_call_json_5xx_still_retries_with_backoff(monkeypatch):
    """别把瞬时故障的快速通道也堵死：5xx / 网络错误仍按原纪律退避重试。"""
    import llm
    from models import SelfCheckPayload

    class Fake500(Exception):
        status_code = 500

    calls = []

    class _Completions:
        def create(self, **_kw):
            calls.append(1)
            raise Fake500("Error code: 500 - internal error")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(llm, "_get_client", lambda *a, **k: _Client())
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    try:
        llm.call_json("sys", "user", SelfCheckPayload, retries=2,
                      api_key="sk-x", base_url="https://x/v1", model="m")
        raise AssertionError("应当抛出 RuntimeError")
    except RuntimeError as e:
        assert "连续失败" in str(e)
    assert len(calls) == 3, "5xx 应保持 retries+1 次尝试"
