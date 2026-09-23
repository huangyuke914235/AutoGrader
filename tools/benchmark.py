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
    python tools/benchmark.py --batch data/results/batch_v2_20260922_120000.json

说明：
    输出**版本化**文件 benchmark_v2_<时间戳>.json，绝不覆盖历史结果。
    结果里记录数据集、rubric 类型、是否启用复核、模型与评测时间——
    没有这些上下文的指标数字是没有意义的。
"""
import os
import sys
import json
import math
import argparse
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GOLD = os.path.join(ROOT, "data", "gold", "gold.json")
BATCH = os.path.join(ROOT, "data", "results", "batch.json")

import metrics                      # 对外指标的唯一计算口径
import parser as P


def pick_batch(path=None):
    """优先指定的；其次 batch_latest；最后退回旧 batch.json"""
    if path:
        return path
    latest = os.path.join(ROOT, "data", "results", "batch_latest.json")
    return latest if os.path.exists(latest) else BATCH


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return None if dx == 0 or dy == 0 else round(num / (dx * dy), 3)


def load_text(rid):
    p = os.path.join(ROOT, "data", "samples", rid + ".txt")
    if not os.path.exists(p):
        return None
    return open(p, encoding="utf-8").read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="指定批次结果文件")
    args = ap.parse_args()
    batch_path = pick_batch(args.batch)

    for p, tip in ((GOLD, "先跑 tools/import_gold.py"), (batch_path, "先跑 tools/batch_run.py")):
        if not os.path.exists(p):
            print(f"缺少 {p}（{tip}）")
            sys.exit(1)

    gold = json.load(open(GOLD, encoding="utf-8"))
    raw = json.load(open(batch_path, encoding="utf-8"))
    # 兼容两种格式：新版带 config/results，旧版是纯列表
    if isinstance(raw, dict):
        cfg = raw.get("config", {})
        results = raw.get("results", [])
    else:
        cfg, results = {"rubric_type": "fixed_5item", "enable_recheck": False}, raw
    ai = {b["report_id"]: b for b in results}
    print(f"批次文件：{os.path.basename(batch_path)}")
    print(f"配置：rubric={cfg.get('rubric_type')}  复核={'开' if cfg.get('enable_recheck') else '关'}"
          f"  模型={cfg.get('model') or '(未记录)'}")

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
    # 注意两件事：
    #   1) 正文要先用 parser.canonical 规范过（判定时用的就是这份文本），
    #      否则拿带 PDF 断行的原文去比，等于自己给自己制造失败。
    #   2) 被校验剔除的引用（dropped）一律计入分母且算不可溯源 ——
    #      可溯源率衡量的是「模型原始产出的引用」有多少能回溯，
    #      只统计幸存者会让这个数字虚高。
    checked = 0
    traceable = 0
    dropped_n = 0
    cache = {}
    records = []
    for r in compared:
        rid = r["report_id"]
        if rid not in cache:
            raw = load_text(rid)
            cache[rid] = P.canonical(raw) if raw else None
        for d in ai[rid]["details"]:
            dropped_n += len(d.get("dropped", []) or [])
            records.append((d, cache[rid] or ""))
    # 口径统一走 metrics.py（界面与 benchmark 不再可能出现两个数）
    checked, traceable = metrics.traceability_from_details(records)
    trace = metrics.traceability_rate(checked, traceable)

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

    # ---- 补充指标：相关性、复核率、失败率、成本 ----
    gv = [r["gold"] for r in compared]
    av = [r["ai"] for r in compared]
    corr = pearson(gv, av)
    n_items = sum(len(ai[r["report_id"]]["details"]) for r in compared)
    n_review = sum(1 for r in compared for d in ai[r["report_id"]]["details"]
                   if d.get("needs_review"))
    n_syserr = sum(1 for r in compared for d in ai[r["report_id"]]["details"]
                   if d.get("system_error"))
    latency = [ai[r["report_id"]].get("elapsed_sec", 0) for r in compared]
    tokens = [ai[r["report_id"]].get("run_info", {}).get("tokens", 0) for r in compared]

    print("\n=== 对外指标（填进主页） ===")
    print(f"  MAE 平均绝对误差：{mae} 分")
    print(f"  误差 ≤5 分占比  ：{acc5}%")
    print(f"  证据可溯源率    ：{trace}%（{traceable}/{checked} 条引用通过原文精确匹配；"
          f"另有 {dropped_n} 条被校验剔除，已计入分母）")
    print(f"  Pearson 相关系数：{corr}")
    print(f"  评分点判定一致率：{item_acc}%（{agree}/{total_items}，仅作参考，不对外宣称）")
    print(f"  人工复核率      ：{round(100*n_review/n_items,1)}%（{n_review}/{n_items} 项）")
    print(f"  系统失败率      ：{round(100*n_syserr/n_items,1)}%（{n_syserr}/{n_items} 项，"
          f"这些不是学生失分）")
    if latency:
        print(f"  单份耗时        ：平均 {round(sum(latency)/len(latency),1)}s"
              f"（合计 {round(sum(latency),1)}s）")
    if any(tokens):
        print(f"  token 合计      ：{sum(tokens)}")

    not_eval = [r["report_id"] for r in rows if r["ai"] is None]
    if not_eval:
        print(f"\n⚠ 未参与评测：{not_eval}（系统因超长跳过，人工分仍在 gold.json 里）")

    # 区分度：把"天花板效应"从主观判断变成可测量的量
    disc = metrics.discrimination([r["ai"] for r in compared])
    # 封存凭证：**原样输出** gold 里的字段，不自己推一个 blind 布尔值出来（那是自证）
    seal = {k: gold.get(k, "") for k in
            ("scorer", "scorer_role", "scored_date", "minutes_spent",
             "independent", "no_ai_reference", "seal_note")}
    missing_seal = [k for k in ("scorer", "scorer_role", "scored_date")
                    if not str(seal.get(k) or "").strip()]
    if missing_seal:
        print(f"\n⚠ 封存记录不完整（缺 {missing_seal}）：这份 gold set 的独立性缺少凭证。")
        print("  补录方法： python tools/import_gold.py --scorer <姓名> --role <专业/分工> "
              "--scored-date <日期> --seal-note 补录")
    else:
        print(f"\n封存凭证：打分人 {seal['scorer']}（{seal['scorer_role']}）"
              f"，日期 {seal['scored_date']}，独立完成={seal['independent']}，"
              f"未参考 AI={seal['no_ai_reference']}")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {
        "version": "v2",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "batch_file": os.path.basename(batch_path),
        "dataset": {"reports": len(compared), "not_evaluated": not_eval,
                    "gold_created_at": gold.get("created_at", ""),
                    "seal_raw": seal,                 # 原样保留，不做布尔化推断
                    "seal_missing_fields": missing_seal},
        "config": cfg,
        "metrics": {"mae": mae, "acc_within_5": acc5, "traceability": trace,
                    "pearson": corr, "item_agreement": item_acc,
                    "review_rate": round(100 * n_review / n_items, 1) if n_items else 0.0,
                    "system_error_rate": round(100 * n_syserr / n_items, 1) if n_items else 0.0,
                    "checked_quotes": checked, "traceable_quotes": traceable,
                    "dropped_quotes": dropped_n,
                    "latency_sec_total": round(sum(latency), 1),
                    "tokens_total": sum(tokens),
                    "discrimination": disc},
        "rows": rows,
    }
    print(f"区分度：满分 {disc['perfect_count']}/{disc['n']} 份"
          f"（{disc['perfect_ratio']}%），极差 {disc['range']}，标准差 {disc['stdev']}")
    p = os.path.join(ROOT, "data", "results", f"benchmark_v2_{ts}.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n已写入 {p}（旧结果未被覆盖）")


if __name__ == "__main__":
    main()
