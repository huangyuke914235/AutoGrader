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
    from openai import OpenAI
    from pydantic import ValidationError

    client = OpenAI(api_key=get_env("LLM_API_KEY"),
                    base_url=get_env("LLM_BASE_URL"),
                    timeout=timeout)
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


def verify_evidence(judgement, full_text: str, min_len: int = 6) -> bool:
    """核弹级校验：AI 给的每一条引用必须在原文中逐字存在。

    这是「让执行者之外的东西来判定」的工程实现。
    返回 False 时必须在 pipeline 里触发重跑，而不是放宽规则。
    """
    if judgement.verdict == "miss":
        return True
    if not judgement.evidence:
        return False
    for ev in judgement.evidence:
        q = (ev.quote or "").strip()
        if len(q) < min_len:
            return False
        if q not in full_text:
            return False
    return True


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
