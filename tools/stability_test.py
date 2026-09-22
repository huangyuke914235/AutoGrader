# -*- coding: utf-8 -*-
"""稳定性测试：同一份报告、同一套评分点，连跑 N 次看结果稳不稳

用法：
    python tools/stability_test.py            # 默认 10 次（单评分点，省额度）
    python tools/stability_test.py 20 --full  # 20 次，用完整 5 项评分点

为什么改成走完整 pipeline：
    旧版本只调用一次模型、只测一个评分点，绕过了 stage_judge 的证据校验与降级逻辑，
    测出来的「稳定」不能代表真实产品行为。现在走 run_grading，统计 verdict 与 score 的波动。

判定标准（写在 02 技术执行步骤里，不许放宽）：
    同一输入连跑 N 次 -> 有效判定率 >= 90%，且引用原文精确匹配率 >= 90%
    另外记录：判定稳定率（多数派占比）、分数极差与标准差
"""
import sys
import os
import json
import time
import statistics
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as P
from models import Rubric, RubricItem
from pipeline import run_grading
from llm import get_env, get_stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(ROOT, "data", "samples")

# 手写的评分点（故意选一个有明确原文依据的，方便检验 quote 能否匹配）
TEST_ITEM = RubricItem(
    id="r1",
    name="实验步骤完整性",
    criteria="是否完整描述了实验的操作步骤或实现过程，而不是只贴最终结果或代码",
    max_score=20,
    positive_signals=["步骤", "实现", "过程", "设计", "流程"],
    negative_signals=["只有结果", "无过程说明"],
)


FULL_ITEMS = [
    RubricItem(id="r1", name="实验目的明确", criteria="开头明确写出本次实验的目的与要掌握的能力",
               max_score=15, positive_signals=["实验目的", "掌握", "目的"]),
    RubricItem(id="r2", name="环境与步骤", criteria="写清实验环境配置与可复现的操作步骤",
               max_score=20, positive_signals=["环境", "步骤", "安装", "配置"]),
    RubricItem(id="r3", name="核心实现", criteria="给出核心代码、模型结构或关键实现说明",
               max_score=25, positive_signals=["代码", "实现", "算法", "结构"]),
    RubricItem(id="r4", name="结果与数据", criteria="给出运行结果、截图、表格或实验数据",
               max_score=20, positive_signals=["结果", "输出", "截图", "数据"]),
    RubricItem(id="r5", name="分析与总结", criteria="对结果进行分析讨论，并有总结或心得",
               max_score=20, positive_signals=["分析", "总结", "心得", "结论"]),
]


def load(sample="S02.txt"):
    return P.parse_file(os.path.join(SAMPLES_DIR, sample))


def main():
    args = [a for a in sys.argv[1:]]
    n = 10
    for a in args:
        if a.isdigit():
            n = int(a)
    use_full = "--full" in args
    items = FULL_ITEMS if use_full else [TEST_ITEM]

    full, sections = load()
    print(f"样本：S02.txt  {len(full)} 字，切出 {len(sections)} 个章节")
    print(f"模型：{get_env('LLM_MODEL')}   评分点：{len(items)} 个"
          f"{'（完整 5 项）' if use_full else '（单点评测，加 --full 可跑完整 5 项）'}")
    print(f"将完整跑 pipeline {n} 次\n" + "=" * 66)

    runs, fails, times = [], [], []
    for i in range(1, n + 1):
        t0 = time.time()
        try:
            res = run_grading(full, sections, "", report_id="S02",
                              rubric=Rubric(items=[x.model_copy(deep=True) for x in items]),
                              enable_recheck=False)
            el = time.time() - t0
            times.append(el)
            runs.append({j.rubric_item_id: (j.verdict, j.score,
                                            [e.quote for e in j.evidence])
                         for j in res.judgements})
            print(f"  {i:>2}/{n}  总分 {res.total:>5.1f}  "
                  + " ".join(f"{j.rubric_item_id}:{j.verdict[:4]}"
                             for j in res.judgements)
                  + f"  {el:>5.1f}s")
        except Exception as e:
            fails.append(f"{type(e).__name__}: {str(e)[:100]}")
            print(f"  {i:>2}/{n}  失败：{str(e)[:80]}")

    print("=" * 66)
    nr = len(runs)
    if not nr:
        print("全部失败，无法评估稳定性。")
        return

    # 引用匹配率：把每次跑出来的引用拿回原文逐字比对
    checked = ok_q = 0
    for r in runs:
        for _iid, (_v, _s, quotes) in r.items():
            for q in quotes:
                checked += 1
                if len(q) >= 6 and q in full:
                    ok_q += 1
    quote_rate = ok_q / checked if checked else 0.0

    # 判定稳定率：每个评分点上，多数派 verdict 占的比例
    stab, score_stats = {}, {}
    for iid in items:
        vs = [r[iid][0] for r in runs if iid in r]
        ss = [r[iid][1] for r in runs if iid in r]
        if not vs:
            continue
        stab[iid] = (max(collections.Counter(vs).values()) / len(vs), dict(collections.Counter(vs)))
        score_stats[iid] = {"score_range": round(max(ss) - min(ss), 1),
                            "score_stdev": round(statistics.pstdev(ss), 2) if len(ss) > 1 else 0.0}
    avg_stab = sum(v[0] for v in stab.values()) / len(stab) if stab else 0.0

    print(f"有效运行率        ：{nr}/{n} = {nr/n*100:.0f}%   （标准 >= 90%）")
    print(f"引用原文匹配率    ：{ok_q}/{checked} = {quote_rate*100:.0f}%   （标准 >= 90%）")
    print(f"判定稳定率（平均）：{avg_stab*100:.1f}%")
    for iid, (rate, dist) in stab.items():
        print(f"    {iid}: {rate*100:>5.1f}%  {dist}  "
              f"分数极差 {score_stats[iid]['score_range']}  "
              f"标准差 {score_stats[iid]['score_stdev']}")
    if times:
        print(f"单次耗时          ：均值 {sum(times)/len(times):.1f}s，合计 {sum(times):.0f}s")
    st = get_stats()
    print(f"Token 消耗        ：{st['tokens']}")
    if fails:
        print(f"失败 {len(fails)} 次：{fails[:2]}")
    print("=" * 66)
    print("生死线判定：" + ("通过 ✅" if (nr / n >= 0.9 and quote_rate >= 0.9) else "未达标 ⚠"))

    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    log = {
        "date": time.strftime("%Y-%m-%d %H:%M"), "runs": n, "items": len(items),
        "valid_run_rate": round(nr / n, 3),
        "quote_match_rate": round(quote_rate, 3),
        "verdict_stability_avg": round(avg_stab, 3),
        "verdict_stability": {k: round(v[0], 3) for k, v in stab.items()},
        "score_stats": score_stats, "tokens": st["tokens"], "fails": fails,
    }
    with open(os.path.join(ROOT, "docs", "stability_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print("结果已写入 docs/stability_log.json（作为开发过程留痕）")


if __name__ == "__main__":
    main()
