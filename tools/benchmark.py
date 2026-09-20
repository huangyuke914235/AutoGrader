# -*- coding: utf-8 -*-
"""D6 benchmark：把系统评分和封存的 gold set 对比，出三个对外指标

三个指标（直接填进 docs/index.html 的 METRICS）：
    1. MAE 平均绝对误差      —— 系统总分与人工总分差的绝对值的平均
    2. 误差 ≤5 分占比        —— 差在 5 分以内的报告比例
    3. 证据可溯源率          —— 判定里带的原文引用，能在本报告正文中精确匹配到的比例

第 3 个指标是硬校验：这里会重新把每条 quote 拿回原文里 find 一遍，
匹配不上就算不可溯源——绝不放宽规则让数字变好看。

前置：
    python tools/batch_run.py      # 先跑出系统评分
    python tools/import_gold.py    # B 成员打分完转 gold.json
然后：
    python tools/benchmark.py
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, "data", "gold", "gold.json")
BATCH = os.path.join(ROOT, "data", "results", "batch.json")
OUT = os.path.join(ROOT, "data", "results", "benchmark.json")


def load_text(rid):
    p = os.path.join(ROOT, "data", "samples", rid + ".txt")
    if not os.path.exists(p):
        return None
    return open(p, encoding="utf-8").read()


def main():
    for p, tip in ((GOLD, "先跑 tools/import_gold.py"), (BATCH, "先跑 tools/batch_run.py")):
        if not os.path.exists(p):
            print(f"缺少 {p}（{tip}）")
            sys.exit(1)

    gold = json.load(open(GOLD, encoding="utf-8"))
    batch = json.load(open(BATCH, encoding="utf-8"))
    ai = {b["report_id"]: b for b in batch}

    rows = []
    for rid in sorted(gold["reports"]):
        g = gold["reports"][rid]
        if rid not in ai:
            rows.append({"report_id": rid, "gold": g["total"], "ai": None, "diff": None,
                         "status": "系统未评（超长跳过）"})
            continue
        rows.append({"report_id": rid, "gold": round(g["total"], 1),
                     "ai": round(ai[rid]["total"], 1),
                     "diff": round(ai[rid]["total"] - g["total"], 1), "status": ""})

    compared = [r for r in rows if r["ai"] is not None]
    if not compared:
        print("没有任何一份报告同时具备人工分与系统分，无法评测")
        sys.exit(1)

    diffs = [abs(r["diff"]) for r in compared]
    mae = round(sum(diffs) / len(diffs), 2)
    acc5 = round(100 * sum(1 for d in diffs if d <= 5) / len(diffs), 1)

    # ---- 证据可溯源率：把每条 quote 拿回原文精确匹配 ----
    checked = 0
    traceable = 0
    cache = {}
    for r in compared:
        rid = r["report_id"]
        if rid not in cache:
            cache[rid] = load_text(rid)
        text = cache[rid]
        for d in ai[rid]["details"]:
            quotes = d.get("evidence", [])
            if not quotes:
                continue
            for q in quotes:
                checked += 1
                if text and q and len(q) >= 6 and q in text:
                    traceable += 1
    trace = round(100 * traceable / checked, 1) if checked else 0.0

    # ---- 逐项：人工判定 vs 系统判定 一致率 ----
    agree = total_items = 0
    for r in compared:
        rid = r["report_id"]
        gitems = gold["reports"][rid]["items"]
        for d in ai[rid]["details"]:
            gid = d["id"]
            if gid in gitems and gitems[gid]["verdict"]:
                total_items += 1
                if gitems[gid]["verdict"] == d["v"]:
                    agree += 1
    item_acc = round(100 * agree / total_items, 1) if total_items else 0.0

    print("\n=== 逐份对比 ===")
    print(f"{'报告':<6}{'人工':>7}{'系统':>8}{'差':>8}   备注")
    for r in rows:
        ai_s = "—" if r["ai"] is None else f"{r['ai']}"
        df = "—" if r["diff"] is None else f"{r['diff']:+}"
        print(f"{r['report_id']:<6}{r['gold']:>7}{ai_s:>8}{df:>8}   {r['status']}")

    print("\n=== 对外指标（填进主页） ===")
    print(f"  MAE 平均绝对误差：{mae} 分")
    print(f"  误差 ≤5 分占比  ：{acc5}%")
    print(f"  证据可溯源率    ：{trace}%（{traceable}/{checked} 条引用通过原文精确匹配）")
    print(f"  评分点判定一致率：{item_acc}%（{agree}/{total_items}，仅作参考，不对外宣称）")

    not_eval = [r["report_id"] for r in rows if r["ai"] is None]
    if not_eval:
        print(f"\n⚠ 未参与评测：{not_eval}（系统因超长跳过，人工分仍在 gold.json 里）")

    json.dump({"mae": mae, "acc": acc5, "trace": trace,
               "item_agreement": item_acc, "n_reports": len(compared),
               "not_evaluated": not_eval, "rows": rows},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n已写入 {OUT}")


if __name__ == "__main__":
    main()
