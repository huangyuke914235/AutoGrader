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


def loads_json(raw: str):
    """宽容 JSON 解析，四级降级的**纯格式**修复（内容一律不改写）：

       ① 严格 json.loads
       ② strict=False      —— 允许字符串里出现未转义的换行等控制字符
       ③ 转义内部裸引号      —— 修复模型写进内容里的半角引号
       ④ 补齐截断的括号      —— 输出被 max_tokens 截断时救回前半段

    为什么可以对 JSON 宽容、却不影响「证据可溯源」这条铁律：
    这四步一个字符都不删改（转义只是让同一个字符合法化），
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
            # 第 0 次（原文 + 严格）成功 = 模型给的就是合法 JSON；
            # 其余都说明动用了修复，计数留档，事后能回答「有多少次是靠修复救回来的」。
            if try_no:
                _stats["json_repaired"] += 1
            return data
    raise last_err


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


def _get_client(timeout: int = 120):
    """复用 OpenAI 客户端（每次新建会带来不必要的连接开销）"""
    from openai import OpenAI
    key = (get_env("LLM_API_KEY"), get_env("LLM_BASE_URL"), timeout)
    if key not in _client_cache:
        _client_cache[key] = OpenAI(api_key=key[0], base_url=key[1], timeout=timeout)
    return _client_cache[key]


def call_json(system: str, user: str, schema, temperature: float = 0.0,
              retries: int = 2, timeout: int = 120, backoff: float = 3.0):
    """调用模型并返回校验通过的 schema 实例。

    四道防线：
    1. response_format=json_object（模型侧）
    2. strip_code_fence + loads_json 四级宽容解析（解析侧，只修格式不改内容）
    3. 把错误回喂给模型重试（重试侧）
    4. 传输层异常退避重试（网络/超时/限流侧）

    第 4 道是 2026-09-22 补的：原实现只捕获 TypeError，任何 APIConnectionError /
    APITimeoutError / RateLimitError / 5xx 都会直接穿透出去，一次抖动就把整阶段打挂，
    而上层又把它吞成一句「生成失败」——线上因此出现过 E 阶段长期失败但查不到原因。
    """
    from pydantic import ValidationError

    # DEMO_MODE 必须真的拦住调用——旧实现只是界面上写一句提示，模型照样会被调用
    if demo_mode():
        raise RuntimeError("DEMO_MODE=true：已阻止调用真实模型。"
                           "如需真实评阅请把 DEMO_MODE 设为 false 并配置密钥。")

    client = _get_client(timeout)      # 复用客户端，不要每次调用都新建
    model = get_env("LLM_MODEL", "deepseek-chat")

    last_err = None
    for attempt in range(retries + 1):
        try:
            _stats["calls"] += 1
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
            except TypeError:
                # 某些模型不支持 response_format，退回普通调用
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=temperature,
                )
        except Exception as e:
            # 传输/服务端瞬时故障：退避后重试，而不是立刻放弃整个阶段
            last_err = e
            _stats["failed"] += 1
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
        # 一条合格引用都没有：不合格的原样留在 evidence（教师界面还能看到模型当时引了什么），
        # 但不再记进 dropped_quotes，否则可溯源率的分母会把同一批引用数两遍。
        judgement.dropped_quotes = []
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
