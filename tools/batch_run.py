# -*- coding: utf-8 -*-
"""批量跑分：用固定 rubric 评阅全部样本，结果写入 data/results/batch.json

为什么用「固定 rubric」：
    gold set 的人工分数是按固定 5 项标准打的（见 data/gold/ 打分表）。
    如果让系统每次动态生成评分点，AI 分和人工分的维度就对不上，MAE 毫无意义。
    所以 D6 benchmark 必须走这里的固定 rubric。

与 gold 打分表的对应关系：
    r1 实验目的明确 15 / r2 环境与步骤 20 / r3 核心实现 25 / r4 结果与数据 20 / r5 分析与总结 20

用法：
    python tools/batch_run.py
"""
import sys
import os
import json

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
    meta_path = os.path.join(ROOT, "data", "samples", "meta.json")
    meta = json.load(open(meta_path, encoding="utf-8"))
    rub = Rubric(items=ITEMS)
    out = []
    skipped = []

    for m in meta:
        rid = m["report_id"]
        if m.get("chars", 0) > MAX_CHARS:
            skipped.append((rid, m["chars"]))
            print(f"{rid} 跳过（{m['chars']} 字 > {MAX_CHARS}，超长会触发检索召回，可能产生假阴性）")
            continue
        full, secs = P.parse_file(os.path.join(ROOT, "data", "samples", rid + ".txt"))
        try:
            res = run_grading(full, secs, "", report_id=rid, rubric=rub, enable_recheck=False)
            details = []
            for j in res.judgements:
                details.append({
                    "id": j.rubric_item_id,
                    "v": j.verdict,
                    "s": j.score,
                    "confidence": j.confidence,
                    "needs_review": j.needs_review,
                    "evidence": [e.quote for e in j.evidence],
                })
            out.append({"report_id": rid, "total": res.total, "chars": m["chars"],
                        "name": str(m.get("original", ""))[:30], "details": details})
            print(f"{rid}  {res.total:>5.1f} 分  {res.elapsed_sec:>5.1f}s  {m.get('original','')[:30]}")
        except Exception as e:
            print(f"{rid} 失败：{str(e)[:100]}")

    tot = [o["total"] for o in out]
    print("\n=== 汇总 ===")
    if tot:
        print(f"参评 {len(out)} 份，分数分布 {sorted(round(t,1) for t in tot)}")
        print(f"最高 {max(tot)} 最低 {min(tot)} 极差 {max(tot)-min(tot)}")
        print("能否区分好坏：", "能 ✅" if max(tot)-min(tot) >= 15 else "不能 ⚠ 判定过于一律")
    if skipped:
        print(f"跳过 {len(skipped)} 份（超长）：{[s[0] for s in skipped]}")

    os.makedirs(os.path.join(ROOT, "data", "results"), exist_ok=True)
    p = os.path.join(ROOT, "data", "results", "batch.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n结果已写入 {p}")


if __name__ == "__main__":
    main()
