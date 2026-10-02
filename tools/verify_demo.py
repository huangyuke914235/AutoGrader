# -*- coding: utf-8 -*-
"""校验预置演示结果的自洽性（构建产物出厂检查）

检的是「如果这里错了，评委一定会看到」的几件事：
1. 每一份都能被 GradingResult 正常还原（模型契约没过 = 界面直接崩）
2. **每条证据都逐字存在于 full_text**（证据是这个产品的卖点，对不上就是自打脸）
3. 总分 = 各评分点分数之和（铁律一：分数由代码加总）
4. meta 里带了 full_text（否则「② 详情对照」的原文高亮没有依据）
5. 每一项都标了待人工复核 / 或明确说明引擎来源（不能把规则判定说成终评）

用法：python tools/verify_demo.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline as PL
from models import GradingResult

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_one(path: str) -> bool:
    name = os.path.basename(path)
    problems = []
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    meta = payload.get("_meta") or {}
    full_text = meta.get("full_text", "")
    try:
        res = GradingResult.model_validate(payload.get("result") or {})
    except Exception as e:
        print(f"[FAIL] {name}: 无法还原成 GradingResult —— {type(e).__name__}: {e}")
        return False

    if not full_text:
        problems.append("_meta.full_text 为空（详情页无法做原文高亮）")

    # 2. 证据逐字校验
    n_ev = 0
    for j in res.judgements:
        for e in j.evidence:
            n_ev += 1
            if e.quote and e.quote not in full_text:
                problems.append(f"证据对不上原文（{j.rubric_item_id}）：{e.quote[:40]!r}")

    # 3. 加总
    s = round(sum(j.score for j in res.judgements), 1)
    if abs(s - res.total) > 0.05:
        problems.append(f"总分不等于各项之和：total={res.total} sum={s}")

    # 4. 待复核标注
    if meta.get("engine") == "rule":
        missing = [j.rubric_item_id for j in res.judgements if not j.needs_review]
        if missing:
            problems.append(f"规则引擎产物未标注待人工复核：{missing}")

    # 5. 满分与评分点一致
    for j in res.judgements:
        it = next((x for x in res.items if x.id == j.rubric_item_id), None)
        if it is None:
            problems.append(f"判定 {j.rubric_item_id} 找不到对应评分点")
        elif j.score > it.max_score + 0.01:
            problems.append(f"{j.rubric_item_id} 得分 {j.score} 超过满分 {it.max_score}")

    status = "OK  " if not problems else "FAIL"
    print(f"[{status}] {name}  总分 {res.total}  评分点 {len(res.judgements)}  "
          f"证据 {n_ev} 条  引擎 {meta.get('engine')}")
    for p in problems:
        print(f"        - {p}")
    return not problems


def main():
    demos = PL.demo_candidates()
    if not demos:
        print("没有找到预置演示结果：先跑 python tools/build_demo.py")
        return 1
    ok = sum(1 for p in demos if check_one(p))
    print(f"\n{ok}/{len(demos)} 份预置结果通过出厂检查")
    return 0 if ok == len(demos) else 1


if __name__ == "__main__":
    sys.exit(main())
