# -*- coding: utf-8 -*-
"""模型调用统一出口 + 防幻觉闸门

这个文件是整个项目最值钱的地方：
AI 输出的引用不由 AI 自己保证，而由 Python 代码用原文精确匹配来判定。
对应创新点二（Evidence Anchoring）与纪律第二条（无证据的判断不算数）。
"""
import json
import os
import re
import time

# 极简 .env 读取（同时兼容 Streamlit secrets）
ROOT = os.path.dirname(os.path.abspath(__file__))
_env = os.path.join(ROOT, ".env")
if os.path.exists(_env):
    with open(_env, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

try:
    import streamlit as st
    _HAS_ST = True
except Exception:
    _HAS_ST = False


def get_env(key, default=""):
    val = os.getenv(key, "")
    if not val and _HAS_ST:
        try:
            val = st.secrets.get(key, "")
        except Exception:
            pass
    return val or default


def demo_mode():
    return str(get_env("DEMO_MODE", "false")).lower() == "true"


def ai_ready():
    """AI 检查是否**真的**能出分。

    为什么需要它：DEMO_MODE=false 且没配密钥时，界面上原本不会有任何提示，
    4 项 AI 检查会静默降级为「未检测」——42% 的权重缺席，而使用者毫不知情，
    会把「只跑了 8 项」当成「跑了 12 项」。凡是显示体检分的地方，
    都必须先问一次这个函数，把缺席情况明写出来。
    """
    if demo_mode():
        return False
    return bool(get_env("LLM_API_KEY", "").strip())


def ai_blocked_reason():
    """返回 AI 不可用的原因文案；可用时返回空串。"""
    if demo_mode():
        return "DEMO_MODE 已开启，不会调用真实模型"
    if not get_env("LLM_API_KEY", "").strip():
        return "未配置 LLM_API_KEY（AI 检查需要大模型接口）"
    return ""


_stats = {"calls": 0, "tokens": 0, "failed": 0, "json_repaired": 0}


def get_stats():
    return dict(_stats)


def escape_inner_quotes(raw: str) -> str:
    """把「字符串内部的裸引号」转义掉 —— 这是 DeepSeek 最常见的一种语病。

    典型坏样本（模型写在 suggestions 里的原文，引号没转义）：
        {"summary":"他在文中写"运行结果示例"但没有贴图"}
    判断规则：字符串里再遇到一个引号时，往后看第一个非空白字符——
      · 是 , } ] : 或已到结尾 → 这是真正的结束引号
      · 否则                → 它是内容里的引号，转义保留（内容不丢，只是不再是“裸”的）
    """
    out, i, n = [], 0, len(raw)
    while i < n:
        if raw[i] != '"':
            out.append(raw[i])
            i += 1
            continue
        out.append('"')          # 字符串起始引号
        i += 1
        while i < n:
            c = raw[i]
            if c == '\\' and i + 1 < n:      # 已经是转义序列，原样搬过去
                out.append(raw[i])
                out.append(raw[i + 1])
                i += 2
                continue
            if c == '"':
                j = i + 1
                while j < n and raw[j] in ' \t\r\n':
                    j += 1
                nxt = raw[j] if j < n else ''
                if nxt in ',}] :' or nxt == '':
                    out.append('"')          # 正常闭合
                    i += 1
                    break
                out.append('\\"')            # 内容里的裸引号 → 转义
                i += 1
                continue
            out.append(c)
            i += 1
    return "".join(out)


def close_truncated(raw: str) -> str:
    """输出被 max_tokens 截断时，补上没闭合的引号与括号，救回前半段。"""
    stack, in_str, esc = [], False, False
    for ch in raw:
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in '[{':
            stack.append(ch)
        elif ch in ']}' and stack:
            stack.pop()
    return raw + ('"' if in_str else '') + "".join('}' if c == '{' else ']' for c in reversed(stack))


def salvage_truncated(raw: str, steps: int = 160):
    """最后一级抢救：从尾部逐步截短，直到能解出一个完整对象。

    用于「结构破损到连补齐括号都救不回来」的情况（例如中间少了个引号、
    或者多出一个不匹配的括号）。宁可拿到前半段可用内容，也不要整段报废。
    """
    dec = json.JSONDecoder()
    n = len(raw)
    if n < 2:
        return None
    step = max(1, n // steps)
    cut = n
    while cut > 1:
        frag = close_truncated(raw[:cut])
        try:
            obj, _ = dec.raw_decode(frag)
        except ValueError:
            cut -= step
            continue
        if isinstance(obj, dict):
            return obj
        cut -= step
    return None


def loads_json(raw: str):
    """宽容 JSON 解析，五级降级的**纯格式**修复（内容一律不改写）：

       ① 严格 json.loads
       ② strict=False        —— 允许字符串里出现未转义的换行等控制字符
       ③ 转义内部裸引号        —— 修复模型写进内容里的半角引号
       ④ 补齐截断的括号        —— 输出被 max_tokens 截断时救回前半段
       ⑤ 渐进截断抢救          —— 结构破损到救不回来时，取能解出的前半段

    第 ⑤ 级是 2026-09-23 补的：E 阶段仍偶发 `Expecting ',' delimiter`
    且四级都救不回来（9 份里约 2 份），与其整段报废，不如拿到前半段可用内容。

    为什么可以对 JSON 宽容、却不影响「证据可溯源」这条铁律：
    这五步一个字符都不删改（转义只是让同一个字符合法化，截断只丢尾部），
    改完的证据引用仍然要去过 verify_evidence 的**原文逐字匹配**；
    救不回来或救歪了的引用会在那里被判死并重跑。换句话说，
    这里放宽的是「格式」，不是「证据」——两件事不能混为一谈。
    """
    last_err = None
    raw = strip_code_fence(raw)          # 先去掉 ```json 围栏与前后闲话
    try_no = 0
    for v in (raw, escape_inner_quotes(raw)):
        fixed = close_truncated(v)
        for txt, strict in ((v, True), (v, False), (fixed, True), (fixed, False)):
            try:
                data = json.loads(txt, strict=strict)
            except ValueError as e:
                last_err = e
                try_no += 1
                continue
            break
        else:
            continue
        break
    else:
        # ⑤ 渐进截断抢救：拿到多少算多少，但必须仍是完整的 JSON 对象
        data = salvage_truncated(raw)
        if data is None:
            raise last_err
        _stats["json_repaired"] += 1
        return data
    if try_no:
        _stats["json_repaired"] += 1
    return data


def strip_code_fence(raw: str) -> str:
    """有些模型会给 ```json ... ```，这里兜底清洗"""
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
    if m:
        raw = m.group(1)
    i, j = raw.find("{"), raw.rfind("}")
    if i >= 0 and j > i:
        raw = raw[i:j + 1]
    return raw.strip()


_client_cache = {}


def _get_client(timeout: int = 120, api_key: str = None, base_url: str = None):
    """复用 OpenAI 客户端（每次新建会带来不必要的连接开销）

    api_key / base_url 为 None 时读环境变量；显式传入时用它。
    为什么要支持显式传参：学生可以自选供应商、自填密钥，
    而 Streamlit 多个会话共享同一份模块 —— 若把学生填的 key 存进模块级变量，
    A 同学的 key 会被 B 同学的会话用到，那是安全事故也是计费事故。
    所以配置只能沿调用链显式传递，绝不落全局。
    """
    from openai import OpenAI
    ak = api_key if api_key is not None else get_env("LLM_API_KEY")
    bu = base_url if base_url is not None else get_env("LLM_BASE_URL")
    cache_key = (ak, bu, timeout)
    if cache_key not in _client_cache:
        _client_cache[cache_key] = OpenAI(api_key=ak, base_url=bu, timeout=timeout)
    return _client_cache[cache_key]


# ---------------- 模型参数差异表 ----------------
# 各家对「采样参数」的容忍度不一样，这里每一条都必须有出处，不许凭印象写。
#
# 依据 Kimi 官方《模型参数参考》（platform.kimi.com/docs/api/models-overview）：
#   「表中『固定』表示该参数不可修改：传入其他值会报错，建议不要显式传入。」
#   kimi-k2.6 / kimi-k2.7-code / kimi-k3 的 temperature、top_p、n 都是固定值
#   （思考模式 temperature=1.0，非思考模式 0.6；top_p 固定 0.95）。
#
# 而我们这条流水线为了结果可复现，一贯传 temperature=0.0
# （见 pipeline.py 的重试判定与 selfcheck.py 的 AI 体检）。
# 两者相遇的实际表现是：**DeepSeek 能用，换 Kimi 就挂** ——
# 前者接受 0，后者直接回 400 invalid_request_error。
# 与其让用户三趟猜谜，不如在这里按模型名显式区分。
FIXED_TEMPERATURE_PREFIXES = ("kimi-k2.", "kimi-k2-", "kimi-k3")


def temperature_allowed(model: str) -> bool:
    """该模型是否接受我们显式传入 temperature。

    返回 False 时必须**整个参数都不传**（不传即使用平台固定值），
    而不是传 None、也不能传 0 —— 传 0 同样会被拒绝。
    """
    m = (model or "").strip().lower()
    return not m.startswith(FIXED_TEMPERATURE_PREFIXES)


def fast_params(model: str) -> dict:
    """该型号有哪些「可以关掉的慢速开关」。返回空 dict 表示没有可关的。

    Kimi k2.6 **默认开启思考模式**（thinking={"type":"enabled"}）：先生成一大段
    推理再作答，同样的任务要多花好几倍时间。而我们的判定任务是「单点判定 +
    原文引用」，并不依赖长链推理 —— 关掉它是纯赚的速度。

    必须**精确到型号，不能按前缀匹配**（与 temperature 那条同源的坑：
    同一协议 ≠ 同一套参数）。依据官方《模型参数参考》：
      · kimi-k2.6      → 支持 disabled / enabled / enabled+keep ✔ 可关
      · kimi-k2.7-code → 仅接受 enabled，传 disabled 会报错 ✘
      · kimi-k3        → 该行为「—」，无此参数 ✘
    宁可只对确认支持的型号生效，也不能为了多覆盖一个型号而把整条通道弄挂。
    """
    m = (model or "").strip().lower()
    if m == "kimi-k2.6":
        return {"thinking": {"type": "disabled"}}
    return {}


def _human_api_error(status: int, e: Exception, model: str) -> str:
    """把 4xx 翻译成人话 + 可执行的出路。密钥绝不带进消息。"""
    raw = str(e)[:300]
    if status == 404:
        return (f"模型接口返回 404：模型名「{model}」在该平台不存在。"
                f"最常见的原因是旧型号已退役（各厂会定期下线旧模型，"
                f"例如 Kimi 的 moonshot-v1 系列已于 2026-08-31 停服）。"
                f"请点下方「测试连接」，它会把这个 Key 真正能用的模型列出来。"
                f"原始报错：{raw}")
    if status in (401, 403):
        _hint = ""
        if model and ("moonshot" in str(model).lower() or "kimi" in str(model).lower()):
            _hint = ("Kimi 有国内 / 国际两个平台、端点互不通用："
                     "国内 platform.moonshot.cn → https://api.moonshot.cn/v1，"
                     "国际 platform.kimi.ai → https://api.moonshot.ai/v1。"
                     "Key 打错平台会返回 401，请换「自定义」通道填另一个端点试试。")
        return (f"密钥被平台拒绝（{status}）：API Key 无效、已过期，"
                f"或账户欠费 / 没有该模型的使用权限。{_hint}"
                f"原始报错：{raw}")
    # 400
    _p = ""
    if "temperature" in raw or "top_p" in raw:
        _p = ("报错指向 temperature / top_p —— 该型号这两个参数是平台固定值，"
              "不接受自定义（Kimi k2.x 与 k3 系列都是这样）。")
    elif "response_format" in raw or "json" in raw.lower():
        _p = ("报错指向 response_format —— 该型号不支持强制 JSON 输出，"
              "请换一个支持 JSON Mode 的型号。")
    return (f"平台拒绝了请求参数（400）：{_p}原始报错：{raw}")


def call_json(system: str, user: str, schema, temperature: float = 0.0,
              retries: int = 2, timeout: int = 120, backoff: float = 3.0,
              api_key: str = None, base_url: str = None, model: str = None,
              fast: bool = True):
    """调用模型并返回校验通过的 schema 实例。

    api_key / base_url / model 为 None 时读环境变量；显式传入时用它
    （学生自带密钥的场景）。详见 _get_client 的注释：配置不落全局。

    四道防线：
    1. response_format=json_object（模型侧）
    2. strip_code_fence + loads_json 四级宽容解析（解析侧，只修格式不改内容）
    3. 把错误回喂给模型重试（重试侧）
    4. 传输层异常退避重试（网络/超时/限流侧）

    第 4 道是 2026-09-22 补的：原实现只捕获 TypeError，任何 APIConnectionError /
    APITimeoutError / RateLimitError / 5xx 都会直接穿透出去，一次抖动就把整阶段打挂，
    而上层又把它吞成一句「生成失败」——线上因此出现过 E 阶段长期失败但查不到原因。

    fast=True（默认）时会带上该型号的「加速参数」（见 fast_params）：
    例如 Kimi k2.6 默认开启思考模式，每次要先吐一大段推理，慢好几倍；
    而我们的判定任务不依赖长链推理，关掉它是纯赚。
    需要最高判分质量时可传 fast=False 打开思考（更慢）。
    """
    from pydantic import ValidationError

    # DEMO_MODE 拦的是「用平台配置去烧钱」：旧实现只是界面上写一句提示，
    # 模型照样会被调用。但学生**显式自带**的密钥属于他自己的账户，应当放行。
    if demo_mode() and api_key is None:
        raise RuntimeError("DEMO_MODE=true：已阻止调用真实模型。"
                           "如需真实评阅请把 DEMO_MODE 设为 false 并配置密钥。")

    client = _get_client(timeout, api_key=api_key, base_url=base_url)
    if model is None:
        model = get_env("LLM_MODEL", "deepseek-chat")

    # 部分模型的 temperature 是平台固定值（Kimi k2.x / k3），传任何自定义值
    # 都会被 400 拒绝 —— 那就整个参数都不传，用它自己的默认值。
    # 注意必须是「不传」而不是「传默认 0.0」：传 0 照样报错。
    # 代价（必须说清楚）：无法再锁 temperature=0，同一份报告两次跑分可能有波动，
    # 所以这类模型的评分稳定性不如 DeepSeek。详见 docs/模型接入设计.md。
    _temp = {"temperature": temperature} if temperature_allowed(model) else {}

    # 厂商私有参数只能走 extra_body（SDK 不认识这些字段，直接当关键字传会 TypeError）。
    _speed = fast_params(model) if fast else {}
    _extra = {"extra_body": _speed} if _speed else {}

    last_err = None
    for attempt in range(retries + 1):
        try:
            _stats["calls"] += 1
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    **_temp, **_extra,
                )
            except TypeError:
                # 某些模型不支持 response_format，退回普通调用
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    **_temp, **_extra,
                )
        except Exception as e:
            last_err = e
            _stats["failed"] += 1
            status = getattr(e, "status_code", None)
            # 4xx（429 限流除外）是请求本身有问题：模型名错了、key 无效、参数不支持，
            # 重试一万次也是同一个错。立刻报人话并给出可执行的出路，
            # 别让学生干等 3s+6s 退避之后才看到一句干巴巴的 NotFoundError。
            # （真实案例：Kimi 的 moonshot-v1 全系 2026-08-31 停服后，
            #   旧预设每次都要转圈 10 秒才报 404。）
            if status in (400, 401, 403, 404):
                raise RuntimeError(_human_api_error(status, e, model))
            # 传输/服务端瞬时故障：退避后重试，而不是立刻放弃整个阶段
            if attempt < retries:
                wait = backoff * (2 ** attempt)
                print(f"[llm] 第 {attempt + 1} 次调用失败（{type(e).__name__}）"
                      f"，{wait:.0f}s 后退避重试：{str(e)[:200]}")
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"模型调用连续失败 {retries + 1} 次（{type(e).__name__}）：{str(e)[:300]}")

        try:
            _stats["tokens"] += (getattr(resp, "usage", None) and resp.usage.total_tokens) or 0
        except Exception:
            pass

        raw = resp.choices[0].message.content or ""
        try:
            data = loads_json(raw)
            return schema.model_validate(data)
        except (ValidationError, ValueError, TypeError) as e:
            last_err = e
            _stats["failed"] += 1
            user = (user + f"\n\n【重要】上一次输出不合法，错误是：{str(e)[:300]}。"
                           f"请严格按要求的 JSON 字段重新输出，不要加任何解释文字。")
            time.sleep(1)

    raise RuntimeError(f"模型输出连续 {retries+1} 次未通过校验：{last_err}")


def split_evidence(judgement, full_text: str):
    """把引用逐条分类：合格 / 不合格，同时把合格引用的形态归一（便于界面高亮）。

    归一必须**两侧对称**：正文解析时做过空白归一，引用也要用同一套规则归一后再比，
    否则多行代码块的引用永远匹配不上（S07 的「核心实现」曾因此被判 0 分）。
    """
    from parser import canonical as _canon
    from models import QUOTE_MIN_LEN, QUOTE_MAX_LEN

    good, dropped = [], []
    for ev in judgement.evidence:
        q = _canon(ev.quote or "")
        if QUOTE_MIN_LEN <= len(q) <= QUOTE_MAX_LEN and q in full_text:
            ev.quote = q
            good.append(ev)
        else:
            dropped.append(q)
    return good, dropped


def verify_evidence(judgement, full_text: str) -> bool:
    """核弹级校验：AI 给的每一条引用必须在原文中逐字存在。

    这是「让执行者之外的东西来判定」的工程实现。
    返回 False 时必须在 pipeline 里触发重跑，而不是放宽规则。

    逐条判定：不合格的引用被剔除，只要还剩至少一条合格引用，这条判定就成立；
    一条都不剩才算不通过。（旧实现是「一条不合格 → 整条判定连同其余合格引用一起作废」，
    实测把 11 个评分点误判成 0 分——那是 bug，不是严格。）

    miss 判定不允许携带证据：直接清空。
    """
    if judgement.verdict == "miss":
        if judgement.evidence:
            judgement.reason = (judgement.reason or "") + \
                "（miss 判定不应携带证据，已清空）"
        judgement.evidence = []
        judgement.dropped_quotes = []
        return True

    if not judgement.evidence:
        return False

    good, dropped = split_evidence(judgement, full_text)

    if not good:
        # 一条合格引用都没有：全部记进 dropped_quotes，**同时清空 evidence**。两个理由：
        #  1) 清空 evidence，避免同一批引用在可溯源率的分母里被数两遍；
        #  2) 记进 dropped，保证"最差的那批引用"仍然计入分母 ——
        #     否则整条降级作废的判定会连同它的引用一起从分母里消失，让可溯源率虚高。
        judgement.dropped_quotes = dropped
        judgement.evidence = []
        return False

    judgement.dropped_quotes = dropped
    if dropped:
        judgement.evidence = good
        brief = "、".join(f"「{d[:12]}」" for d in dropped[:3])
        judgement.reason = (judgement.reason or "") + \
            f"（另有 {len(dropped)} 条引用未通过原文逐字校验，已剔除：{brief}）"
    return True


# verdict 与 score 的合法对应关系（P1-1 的最小方案：不取消模型打分，但检测矛盾）
SCORE_BANDS = {
    "hit": (0.70, 1.00),        # hit 至少拿到该点 70% 的分
    "partial": (0.15, 0.85),
    "miss": (0.00, 0.00),
}


def normalize_judgement(judgement, item, full_text: str):
    """统一的判定规范化入口 —— 所有分支都必须过这一道，不再各写各的。

    做的四件事：
    1. verdict 非法 → 归为 partial 并标记待复核（不静默丢弃）
    2. confidence / score 夹到合法区间
    3. miss → 强制 0 分且无证据；非 miss → 必须至少一条合法证据（否则由调用方降级）
    4. verdict 与 score 明显矛盾 → 标记待复核并写明原因（**不偷偷改分**）

    返回 (judgement, notes)；notes 是需要写进 reason 的说明。
    """
    from models import VERDICTS

    notes = []
    max_score = float(getattr(item, "max_score", 100) or 100)

    if judgement.verdict not in VERDICTS:
        notes.append(f"判定值 {judgement.verdict!r} 非法，已归为 partial 并转人工复核")
        judgement.verdict = "partial"
        judgement.needs_review = True

    if judgement.confidence is None or isinstance(judgement.confidence, bool):
        judgement.confidence = 0.0
    try:
        judgement.confidence = max(0.0, min(1.0, float(judgement.confidence)))
    except (TypeError, ValueError):
        judgement.confidence = 0.0
        notes.append("confidence 不是数字，已归零并转人工复核")
        judgement.needs_review = True

    try:
        judgement.score = max(0.0, min(float(judgement.score), max_score))
    except (TypeError, ValueError):
        judgement.score = 0.0
        notes.append("score 不是数字，已归零并转人工复核")
        judgement.needs_review = True

    if judgement.verdict == "miss":
        if judgement.score != 0:
            notes.append("miss 判定必须是 0 分，已强制归零")
        judgement.score = 0.0
        if judgement.evidence:
            notes.append("miss 判定不应携带证据，已清空")
        judgement.evidence = []

    # verdict 与 score 是否互相矛盾（只标记，不擅自改分）
    lo, hi = SCORE_BANDS[judgement.verdict]
    ratio = judgement.score / max_score if max_score else 0.0
    if not (lo - 1e-6 <= ratio <= hi + 1e-6):
        judgement.needs_review = True
        notes.append(
            f"判定 {judgement.verdict} 与得分 {judgement.score}/{max_score} 不匹配"
            f"（该判定应落在 {lo:.0%}~{hi:.0%}），已转人工复核")

    return judgement, notes


_SECRET_RE = re.compile(r"\b(sk|ak)-[A-Za-z0-9_\-]{8,}")


def _strip_secret(text: str, *secrets: str) -> str:
    """把文本里出现的密钥原文换成掩码。

    诊断的意义就在于「把平台的原话带回来给人看」，但也正因为这样才有风险：
    这份结果大概率会被整段复制粘贴到群里求助 —— 那等于把密钥贴了出去。

    两道防线：
    1. 精确替换已知的密钥原文；
    2. 形态兜底 —— 平台有时会把 key 的一部分回显进错误信息
       （"Invalid key: sk-abc…"），精确替换覆盖不到这种情况。

    保留几位是个取舍：界面回显保留末 4 位，是为了让人一眼确认自己填的是
    哪个 Key（providers.mask_key 干的活）；但**诊断结果是要离开本机的**，
    所以这里一位都不留。同一段数据、两种场景，标准不同，别混为一谈。
    """
    out = text or ""
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, f"{s[:3]}{'*' * 8}")
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}-{'*' * 8}", out)


def diagnose(api_key=None, base_url=None, model=None, timeout: int = 30) -> dict:
    """一键体检模型通道 —— 把平台说的话原样带回来，而不是让人猜。

    为什么必须有它：模型退役、Key 与平台不匹配、账户欠费、某型号不接受
    temperature、不支持 JSON 强输出……这些故障的**表现形式完全一样**，
    都是「跑不出来」。只能看到一句笼统提示的人要往返三轮才可能说清，
    而远程协助又更难。这里把每一步的真实返回结构化输出，分诊一眼完成。

    返回 dict：ok / steps（每一步的名称、成败、原话）/ models（该 Key 能用的模型）
    / hint（给人看的分诊结论）。所有文本均已脱敏。

    设计细节：步骤 ③ 故意**沿用业务真正会传的 temperature**。于是
    「③ 通、④ 不通」或「② 通、③ 不通」就能直接锁定问题落在哪一层，
    反之则排除 —— 这个对照正是上一轮 Kimi 故障最难定位的地方。
    """
    result = {"ok": False, "model": model or "", "base_url": base_url or "",
              "steps": [], "models": [], "hint": ""}
    if not model:
        model = get_env("LLM_MODEL", "")
    if not base_url:
        base_url = get_env("LLM_BASE_URL", "") or None
    if api_key is None:
        api_key = get_env("LLM_API_KEY", "")
    tok = api_key or ""
    result["model"] = model
    result["base_url"] = base_url or ""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    except Exception as e:
        result["steps"].append(("初始化客户端", False, _strip_secret(str(e)[:300], tok)))
        result["hint"] = "客户端没建起来：检查接口地址是不是合法的 http(s) 地址。"
        return result

    # ① 拉取可用模型列表 —— 这一步能同时验出 Key 是否有效、端点是否配错
    try:
        data = getattr(client.models.list(), "data", []) or []
        ids = sorted({getattr(m, "id", "") for m in data if getattr(m, "id", "")})
        result["models"] = ids
        result["steps"].append(("① 拉取可用模型列表", True, f"成功，共 {len(ids)} 个"))
    except Exception as e:
        msg = _strip_secret(str(e)[:300], tok)
        sc = getattr(e, "status_code", None)
        hint = ""
        if sc in (401, 403):
            hint = ("Key 没通过平台认证。请逐个排查：① Key 是否复制完整（前后别带空格）；"
                    "② Key 所属的站点是否和这里填的接口地址匹配 —— Kimi 国内站 "
                    "platform.moonshot.cn 与国际站 platform.kimi.ai 的 Key 互不通用，"
                    "端点分别是 api.moonshot.cn 和 api.moonshot.ai；"
                    "③ 账户是否余额不足或被停用。")
        elif sc == 404:
            hint = "该端点没有 /v1/models 接口。多数情况下对话仍可用，继续看下一步。"
        else:
            hint = "连不上服务器：地址写错、网络不通，或本机需要走代理。"
        result["steps"].append(("① 拉取可用模型列表", False, msg))
        result["hint"] = hint
        if sc in (401, 403):
            return result          # 认证层都没过，后面不必再试

    # ② 模型名是否真的在这个 Key 的名下
    if result["models"] and model:
        if model in result["models"]:
            result["steps"].append(("② 检查模型名", True, f"「{model}」在可用列表里"))
        else:
            result["steps"].append(("② 检查模型名", False,
                                    f"这个 Key 名下没有「{model}」"))
            result["hint"] = ("请把模型名改成下方可用列表里的某一个"
                              "（Kimi 现役通用型号为 kimi-k2.6 / kimi-k3）。")

    # ③ 最小对话：参数与业务实际调用保持一致
    _temp = {"temperature": 0.0} if temperature_allowed(model) else {}
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复 OK 两个字"}],
            **_temp,
        )
        txt = (resp.choices[0].message.content or "").strip()[:80]
        _p = "temperature=0" if _temp else "未传 temperature（该型号此项为平台固定值）"
        result["steps"].append(("③ 最小对话", True, f"返回：{txt}（{_p}）"))
        result["ok"] = True
    except Exception as e:
        msg = _strip_secret(str(e)[:300], tok)
        sc = getattr(e, "status_code", None)
        result["steps"].append(("③ 最小对话", False, msg))
        result["hint"] = (_human_api_error(sc, e, model)
                          if sc in (400, 401, 403, 404)
                          else f"调用失败：{msg}")
        return result

    # ④ 业务真正依赖的是 JSON 强输出，单独再验一次
    try:
        r2 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": '只输出 JSON：{"a":1}'}],
            response_format={"type": "json_object"},
            **_temp,
        )
        c2 = (r2.choices[0].message.content or "").strip()[:120]
        loads_json(c2)
        result["steps"].append(("④ JSON 强输出", True, f"解析通过：{c2}"))
    except Exception as e:
        result["steps"].append(("④ JSON 强输出", False, _strip_secret(str(e)[:300], tok)))
        result["hint"] = ("该型号不接受 response_format=json_object。"
                          "代码已能在输出不合法时自动重试，但可靠性下降，建议换个型号。")
        result["ok"] = False

    return result


def locate(section_list, full_text: str, quote: str):
    """给 quote 补上 char_start 与所属 section"""
    pos = full_text.find(quote)
    sec = ""
    if section_list:
        for s in section_list:
            if s.char_start <= pos < s.char_end:
                sec = s.id
                break
    return sec, pos
