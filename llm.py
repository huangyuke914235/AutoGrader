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


_stats = {"calls": 0, "tokens": 0, "failed": 0}


def get_stats():
    return dict(_stats)


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
              retries: int = 2, timeout: int = 120):
    """调用模型并返回校验通过的 schema 实例。

    三道防线：
    1. response_format=json_object（模型侧）
    2. strip_code_fence + Pydantic 校验（解析侧）
    3. 把错误回喂给模型重试（重试侧）
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
        try:
            _stats["tokens"] += (getattr(resp, "usage", None) and resp.usage.total_tokens) or 0
        except Exception:
            pass

        raw = resp.choices[0].message.content or ""
        try:
            return schema.model_validate_json(strip_code_fence(raw))
        except (ValidationError, ValueError) as e:
            last_err = e
            user = (user + f"\n\n【重要】上一次输出不合法，错误是：{str(e)[:300]}。"
                           f"请严格按要求的 JSON 字段重新输出，不要加任何解释文字。")
            time.sleep(1)

    _stats["failed"] += 1
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
