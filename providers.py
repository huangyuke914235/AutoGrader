# -*- coding: utf-8 -*-
"""模型供应商注册表 —— 学生自己选、自己填 key 的那张表

设计取舍：

1. **不用全局状态**。曾经考虑过在 llm.py 里放一个模块级 `_RUNTIME` 字典，
   界面改一下就全局生效。但 Streamlit 每个会话是独立线程、共享同一份模块，
   那样做等于「A 同学填的 key 会被 B 同学用掉」，既是安全事故也是计费事故。
   所以配置**只能显式传参**：界面 → run_selfcheck(llm_cfg=...) → call_json(...)，
   全程不落模块级变量。

2. **默认通道是「离线规则」，不是某个免费模型**。
   不存在真正免费、无需注册、无限额的通用大模型 API。与其给用户一个随时会挂
   的第三方免费接口，不如把「8 项规则（58 权重）」作为永远可用的兜底 ——
   它零配置、零成本、永不失效，而且我们已经把它做扎实了。

3. 平台共享额度（管理员配 key、学生免费用）是**可选**的，默认关闭，
   且必须带限额，否则一个公开课链接就能刷爆账单。
"""

# 每个供应商：id / 显示名 / base_url / 默认模型 / 申请地址 / 是否必须填 key / 一句话说明
PROVIDERS = [
    {
        "id": "offline",
        "name": "仅离线规则（免费·无需任何配置）",
        "base_url": "",
        "model": "",
        "key_url": "",
        "needs_key": False,
        "note": "8 项规则，58/100 权重。零成本、结果可复现，永不失效",
        "free": True,
    },
    {
        "id": "deepseek",
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_url": "https://platform.deepseek.com/api_keys",
        "needs_key": True,
        "note": "中文强、便宜，约 ¥1 / 百万 token。推荐首选",
    },
    {
        "id": "qwen",
        "name": "通义千问 Qwen（阿里云）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
        "needs_key": True,
        "note": "国内直连稳定，有免费额度",
    },
    {
        "id": "moonshot",
        "name": "Kimi 月之暗面",
        "base_url": "https://api.moonshot.cn/v1",
        # moonshot-v1 全系已于 2026-08-31 下线（调用直接 404），
        # 现役通用型号：kimi-k2.6（便宜）/ kimi-k3（旗舰）。
        "model": "kimi-k2.6",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "needs_key": True,
        "note": "长文本友好。旧型号 moonshot-v1 已停服，现役为 kimi-k2.6 / kimi-k3",
    },
    {
        "id": "glm",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "needs_key": True,
        "note": "glm-4-flash 有免费额度",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
        "needs_key": True,
        "note": "需自备网络环境",
    },
    {
        "id": "ollama",
        "name": "本地 Ollama（不出校）",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
        "key_url": "https://ollama.com/download",
        "needs_key": False,
        "note": "数据完全不出本机，适合有数据合规要求的场景。需先装并拉起模型",
        "placeholder_key": "ollama",
    },
    {
        "id": "custom",
        "name": "自定义（任意 OpenAI 兼容接口）",
        "base_url": "",
        "model": "",
        "key_url": "",
        "needs_key": True,
        "note": "校内私有化推理服务填这里即可，代码不用改",
    },
]

PROVIDER_IDS = [p["id"] for p in PROVIDERS]

# 各厂已退役、但用户可能从旧教程 / 旧配置里抄来的模型名 → 建议替换型号。
# 在 validate 里提前拦下：否则学生要等三次退避（约 10 秒）才看到一句干巴巴的 404。
# 依据：Moonshot 官方模型列表（platform.moonshot.cn/docs/pricing/chat-v1），
# moonshot-v1 全系 2026-08-31 停服、kimi-k2 系 2026-05-25 停服。
DEAD_MODELS = {
    "moonshot-v1-8k": "kimi-k2.6",
    "moonshot-v1-32k": "kimi-k2.6",
    "moonshot-v1-128k": "kimi-k2.6",
    "moonshot-v1-auto": "kimi-k2.6",
    "moonshot-v1-8k-vision-preview": "kimi-k3",
    "moonshot-v1-32k-vision-preview": "kimi-k3",
    "moonshot-v1-128k-vision-preview": "kimi-k3",
    "kimi-latest": "kimi-k3",
    "kimi-thinking-preview": "kimi-k3",
    "kimi-k2-0711-preview": "kimi-k3",
    "kimi-k2-0905-preview": "kimi-k3",
    "kimi-k2-turbo-preview": "kimi-k3",
    "kimi-k2-thinking": "kimi-k3",
    "kimi-k2-thinking-turbo": "kimi-k3",
    "kimi-k2.5": "kimi-k3",
}


def get(pid: str):
    for p in PROVIDERS:
        if p["id"] == pid:
            return p
    return PROVIDERS[0]


def mask_key(key: str) -> str:
    """显示时脱敏：只留末尾 4 位。日志、导出、报错里一律用这个。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}{'*' * 6}{key[-4:]}"


def build_cfg(pid: str, api_key: str = "", base_url: str = "",
              model: str = "") -> dict:
    """把界面上的选择整理成 llm 能吃的配置。

    返回的 dict 里 **没有** 任何需要保密之外的东西；key 原样保留是因为调用要用，
    但调用方必须保证它只在当前会话/当前请求里流转，不落盘、不进日志。
    """
    p = get(pid)
    if pid == "offline":
        return {"id": "offline", "api_key": "", "base_url": "", "model": ""}
    return {
        "id": pid,
        "api_key": (api_key or "").strip() or p.get("placeholder_key", ""),
        "base_url": (base_url or "").strip() or p.get("base_url", ""),
        "model": (model or "").strip() or p.get("model", ""),
    }


def validate(cfg: dict) -> str:
    """返回错误原因；没问题返回空串。用于在发起调用前挡住无效配置，
    避免让学生等 60 秒超时才看到一句看不懂的报错。"""
    if not cfg or cfg.get("id") == "offline":
        return ""       # 离线通道永远合法
    miss = []
    if not cfg.get("api_key"):
        miss.append("API Key")
    if not cfg.get("base_url"):
        miss.append("接口地址 Base URL")
    if not cfg.get("model"):
        miss.append("模型名")
    if miss:
        return "缺少：" + "、".join(miss)
    if not str(cfg["base_url"]).startswith(("http://", "https://")):
        return "接口地址必须以 http:// 或 https:// 开头"
    dead = DEAD_MODELS.get(str(cfg.get("model", "")).strip())
    if dead:
        return (f"模型「{cfg['model']}」已退役（该平台已停服此型号，调用会返回 404），"
                f"请把模型名改成 {dead}（或该平台当前在售型号）")
    return ""


def describe(cfg: dict) -> str:
    """给用户看的一句话摘要（已脱敏），用于界面回显和导出文件头。"""
    if not cfg or cfg.get("id") == "offline":
        return "离线规则引擎（未接入大模型）"
    if cfg.get("id") == "shared":
        return "平台提供额度（学生免配置）"
    p = get(cfg.get("id", "custom"))
    return f"{p['name']} · 模型 {cfg.get('model') or '-'} · Key {mask_key(cfg.get('api_key',''))}"


def llm_kwargs(cfg: dict) -> dict:
    """把通道配置翻译成 llm.call_json 的**显式参数**（api_key / base_url / model）。

    返回 `{}` 表示「不显式传密钥」——此时 llm 层退回读环境变量，
    并且 DEMO_MODE 依然保有拦截效力（防止用平台配置误烧钱）。

    为什么必须有这个函数（2026-09-24 补）：
    llm.call_json 里那条纪律是「学生显式自带的密钥属于他自己的账户，应当放行」，
    但调用方有 5 处（R/A/A-重试/G/E），旧实现每处都自己 `call_json(...)` 不传参，
    于是侧边栏填了 key 也没人接住 —— 界面显示「已就绪」，一点评阅却被
    DEMO_MODE 拦下，报错还指向一个跟用户操作无关的开关。**一处判断只写一遍**，
    调用方只负责把 llm_cfg 透传下来，不再各自识别 offline、各自拼字典。
    """
    if not cfg or cfg.get("id") == "offline":
        return {}
    if validate(cfg):
        return {}       # 配置不全：宁可不传，也不要传半成品密钥进去
    return {
        "api_key": cfg.get("api_key", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
    }


# ---------------- 平台共享额度（可选）----------------
# 管理员在环境变量里配一个 key，学生就不用自己填。必须显式开启（ENABLE_SHARED_AI），
# 且必须带限额 —— 否则一个公开链接就能刷爆账单。
SHARED_ID = "shared"


def shared_available() -> bool:
    """平台共享额度是否可用：管理员配了 key 且显式开启。"""
    import os
    if str(os.getenv("ENABLE_SHARED_AI", "false")).lower() != "true":
        return False
    return bool(os.getenv("SHARED_LLM_API_KEY", "").strip())


def shared_cfg() -> dict:
    """平台共享通道的配置。没有共享额度时返回 None。"""
    import os
    if not shared_available():
        return None
    return {
        "id": SHARED_ID,
        "api_key": os.getenv("SHARED_LLM_API_KEY", "").strip(),
        "base_url": os.getenv("SHARED_LLM_BASE_URL", "").strip()
                    or os.getenv("LLM_BASE_URL", ""),
        "model": os.getenv("SHARED_LLM_MODEL", "").strip()
                 or os.getenv("LLM_MODEL", "deepseek-chat"),
    }


def daily_limit() -> int:
    """共享通道每会话每日可用次数；0 表示不限（不推荐）。"""
    import os
    try:
        return int(os.getenv("SHARED_DAILY_LIMIT", "20"))
    except ValueError:
        return 20
