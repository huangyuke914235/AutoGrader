# -*- coding: utf-8 -*-
"""批量跑分：用固定 rubric 评阅全部样本，结果写入 data/results/batch.json

为什么用「固定 rubric」：
    gold set 的人工分数是按固定 5 项标准打的（见 data/gold/ 打分表）。
    如果让系统每次动态生成评分点，AI 分和人工分的维度就对不上，MAE 毫无意义。
    所以 D6 benchmark 必须走这里的固定 rubric。

与 gold 打分表的对应关系：
    r1 实验目的明确 15 / r2 环境与步骤 20 / r3 核心实现 25 / r4 结果与数据 20 / r5 分析与总结 20

用法：
    python tools/batch_run.py                 # 默认跑完整流水线（含 G 阶段复核）
    python tools/batch_run.py --no-recheck    # 只跑 A 阶段（结果文件里会明确记录）

说明：
    输出**版本化**文件 batch_v2_<时间戳>.json，同时写一个 batch_latest.json 供 benchmark 读取。
    旧的 batch.json 不会被覆盖——历史结果是审计证据，必须留着。
"""
import sys
import os
import json
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as P
from models import Rubric, RubricItem
from pipeline import run_grading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# build_context 对 ≤40000 字直接给全文（正确性优先），所以阈值取 40000 是安全的
MAX_CHARS = 40000

ITEMS = [
    RubricItem(id="r1", name="实验目的明确", criteria="开头明确写出本次实验的目的与要掌握的能力",
               max_score=15, positive_signals=["实验目的", "旨在", "掌握", "目的"]),
    RubricItem(id="r2", name="环境与步骤", criteria="写清实验环境配置与可复现的操作步骤",
               max_score=20, positive_signals=["环境", "步骤", "安装", "配置", "命令"]),
    RubricItem(id="r3", name="核心实现", criteria="给出核心代码、模型结构或关键实现说明",
               max_score=25, positive_signals=["代码", "实现", "模型", "算法", "结构"]),
    RubricItem(id="r4", name="结果与数据", criteria="给出运行结果、截图、表格或实验数据",
               max_score=20, positive_signals=["结果", "输出", "截图", "数据", "表"]),
    RubricItem(id="r5", name="分析与总结", criteria="对结果进行分析讨论，并有总结或心得",
               max_score=20, positive_signals=["分析", "总结", "心得", "讨论", "结论"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-recheck", action="store_true",
                    help="关闭 G 阶段复核（结果文件里会明确记录，不能假装跑过）")
    args = ap.parse_args()
    enable_recheck = not args.no_recheck

    meta_path = os.path.join(ROOT, "data", "samples", "meta.json")
    meta = json.load(open(meta_path, encoding="utf-8"))
    rub = Rubric(items=ITEMS)
    out = []
    skipped = []
    failed = []

    for m in meta:
        rid = m["report_id"]
        if m.get("chars", 0) > MAX_CHARS:
            skipped.append({"report_id": rid, "chars": m["chars"]})
            print(f"{rid} 跳过（{m['chars']} 字 > {MAX_CHARS}，超长会触发检索召回，可能产生假阴性）")
            continue
        full, secs = P.parse_file(os.path.join(ROOT, "data", "samples", rid + ".txt"))
        try:
            res = run_grading(full, secs, "", report_id=rid, rubric=rub,
                              enable_recheck=enable_recheck)
            details = []
            for j in res.judgements:
                details.append({
                    "id": j.rubric_item_id,
                    "v": j.verdict,
                    "s": j.score,
                    "confidence": j.confidence,
                    "needs_review": j.needs_review,
                    "system_error": j.system_error,
                    "evidence": [e.quote for e in j.evidence],
                    # 被原文校验剔除的引用也要留档：可溯源率必须按「模型原始产出」算，
                    # 只统计存活下来的引用会让这个数字虚高
                    "dropped": list(getattr(j, "dropped_quotes", []) or []),
                })
            out.append({
                "report_id": rid, "total": res.total, "ai_total": res.ai_total,
                "chars": m["chars"], "name": str(m.get("original", ""))[:30],
                "elapsed_sec": res.elapsed_sec, "details": details,
                "run_info": json.loads(res.run_info.model_dump_json()) if res.run_info else {},
            })
            print(f"{rid}  {res.total:>5.1f} 分  {res.elapsed_sec:>5.1f}s  {m.get('original','')[:30]}")
        except Exception as e:
            failed.append({"report_id": rid, "error": f"{type(e).__name__}: {e}"})
            print(f"{rid} 失败：{str(e)[:100]}")

    tot = [o["total"] for o in out]
    print("\n=== 汇总 ===")
    if tot:
        print(f"参评 {len(out)} 份，分数分布 {sorted(round(t, 1) for t in tot)}")
        print(f"最高 {max(tot)} 最低 {min(tot)} 极差 {max(tot) - min(tot)}")
        print("能否区分好坏：", "能 ✅" if max(tot) - min(tot) >= 15 else "不能 ⚠ 判定过于一律")
    if skipped:
        print(f"跳过 {len(skipped)} 份（超长）：{[s['report_id'] for s in skipped]}")
    if failed:
        print(f"失败 {len(failed)} 份：{[f['report_id'] for f in failed]}")

    os.makedirs(os.path.join(ROOT, "data", "results"), exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "version": "v2",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "config": {
            "rubric_type": "fixed_5item",
            "enable_recheck": enable_recheck,
            "max_chars": MAX_CHARS,
            "model": os.getenv("LLM_MODEL", ""),
            "reports_total": len(meta),
            "reports_scored": len(out),
        },
        "skipped": skipped,
        "failed": failed,
        "results": out,
    }
    p = os.path.join(ROOT, "data", "results", f"batch_v2_{ts}.json")
    json.dump(payload, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    latest = os.path.join(ROOT, "data", "results", "batch_latest.json")
    json.dump(payload, open(latest, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n结果已写入 {p}（旧结果未被覆盖）")


if __name__ == "__main__":
    main()
