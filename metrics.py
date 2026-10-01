# -*- coding: utf-8 -*-
"""对外指标的**唯一**计算口径

为什么要单独抽一个模块：
    同一份数据在界面和 benchmark 里算出两个数（界面只数"幸存的引用"，
    而幸存引用是已经通过校验的，结构上恒为 ~100%），这种不一致比数字难看更致命。
    所以：**所有对外指标只允许从这里算**。

证据可溯源率的口径（写死，不允许各写各的）：
    分母 = 模型产出的**全部**引用，包括被原文校验剔除的、以及整条判定降级作废的；
    分子 = 能在对应报告正文中逐字匹配到的引用。
    —— 只有把"不合格的那批"也算进分母，这个指标才代表模型的真实表现；
    只数幸存者会让它虚高到接近 100%，那是自欺。
"""
from typing import Iterable, Tuple

from models import QUOTE_MIN_LEN, QUOTE_MAX_LEN      # 与 verify_evidence **同一套**长度规则


def traceability(quotes: Iterable[str], text: str) -> Tuple[int, int]:
    """(checked, ok)：单份报告的可溯源计数

    合格的定义必须与 `llm.verify_evidence` 完全一致：**长度在 6~200 之间、且在正文中逐字存在**。
    少了上限这一条，那些超长（>200 字）的引用会被算成"可溯源"，
    于是同一个数据算出 98.4% 与 96.9% 两个数——指标口径不允许有两套。
    """
    checked = ok = 0
    for q in quotes:
        if not q:
            continue
        checked += 1
        if QUOTE_MIN_LEN <= len(q) <= QUOTE_MAX_LEN and q in (text or ""):
            ok += 1
    return checked, ok


def all_quotes_of(judgement) -> list:
    """一条判定中，模型产出的全部引用（保留的 + 被剔除的）"""
    kept = [e.quote for e in getattr(judgement, "evidence", []) if e.quote]
    dropped = list(getattr(judgement, "dropped_quotes", []) or [])
    return kept + dropped


def traceability_from_judgements(pairs) -> Tuple[int, int]:
    """pairs: [(judgement, 该报告的正文)] —— 界面用它，与 benchmark 口径一致"""
    checked = ok = 0
    for judgement, text in pairs:
        c, o = traceability(all_quotes_of(judgement), text)
        checked += c
        ok += o
    return checked, ok


def traceability_from_details(records) -> Tuple[int, int]:
    """records: [(batch 明细 dict, 该报告的正文)]

    明细里的 evidence 是保留的引用、dropped 是被校验剔除的；
    两者都算进分母（降级作废的判定，其引用在被清空后也全在 dropped 里）。
    """
    checked = ok = 0
    for detail, text in records:
        quotes = list(detail.get("evidence", []) or []) + list(detail.get("dropped", []) or [])
        c, o = traceability(quotes, text)
        checked += c
        ok += o
    return checked, ok


def traceability_rate(checked: int, ok: int) -> float:
    return round(100.0 * ok / checked, 1) if checked else 0.0


def discrimination(totals) -> dict:
    """区分度：天花板效应的量化（满分份数 / 极差 / 标准差）

    光说"高分区区分度不足"是主观判断，这里把它变成能被测量的量。
    """
    vals = [float(t) for t in totals]
    n = len(vals)
    if n == 0:
        return {"n": 0, "max": 0.0, "min": 0.0, "range": 0.0, "stdev": 0.0,
                "perfect_count": 0, "perfect_ratio": 0.0}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    perfect = sum(1 for v in vals if v >= 99.999)
    return {
        "n": n,
        "max": round(max(vals), 1),
        "min": round(min(vals), 1),
        "range": round(max(vals) - min(vals), 1),
        "stdev": round(var ** 0.5, 2),
        "perfect_count": perfect,
        "perfect_ratio": round(100.0 * perfect / n, 1),
    }
