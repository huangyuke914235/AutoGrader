# -*- coding: utf-8 -*-
"""生成 GitHub Pages 主页用的离线案例 JSON

    source .venv/Scripts/activate
    python tools/make_demo.py            # 默认 S02 S03 S04
    python tools/make_demo.py S01 S05    # 指定报告

输出 docs/cases/S0x.json —— 即使评委打不开在线 demo，主页也能展示完整证据链。
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as P
from models import Rubric, RubricItem
from pipeline import run_grading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "docs", "cases")

ITEMS = [
    ("r1", "实验目的明确", "开头明确写出本次实验的目的与要掌握的能力", 15,
     ["实验目的", "旨在", "掌握", "目的"]),
    ("r2", "环境与步骤", "写清实验环境配置与可复现的操作步骤", 20,
     ["环境", "步骤", "安装", "配置", "命令"]),
    ("r3", "核心实现", "给出核心代码、模型结构或关键实现说明", 25,
     ["代码", "实现", "模型", "算法", "结构"]),
    ("r4", "结果与数据", "给出运行结果、截图、表格或实验数据", 20,
     ["结果", "输出", "截图", "数据", "表"]),
    ("r5", "分析与总结", "对结果进行分析讨论，并有总结或心得", 20,
     ["分析", "总结", "心得", "讨论", "结论"]),
]


def make_rubric():
    return Rubric(items=[RubricItem(id=i, name=n, criteria=c, max_score=s,
                                    positive_signals=p) for i, n, c, s, p in ITEMS])


def main():
    ids = sys.argv[1:] or ["S02", "S03", "S04"]
    os.makedirs(CASES, exist_ok=True)
    for rid in ids:
        path = os.path.join(ROOT, "data", "samples", rid + ".txt")
        if not os.path.exists(path):
            print(f"跳过 {rid}（无样本）")
            continue
        full, secs = P.parse_file(path)
        print(f"正在评阅 {rid}（{len(full)} 字）...", flush=True)
        res = run_grading(full, secs, "", report_id=rid,
                          rubric=make_rubric(), enable_recheck=True)
        data = {
            "report_id": res.report_id,
            "filename": rid + ".txt",
            "total_score": res.total,
            "full_text": full,
            "items": [{"id": it.id, "name": it.name, "max_score": it.max_score}
                      for it in res.items],
            "judgements": [{
                "rubric_item_id": j.rubric_item_id,
                "verdict": j.verdict,
                "score": j.score,
                "confidence": j.confidence,
                "reason": j.reason,
                "needs_review": j.needs_review,
                "evidence": [{"section_id": e.section_id, "quote": e.quote,
                              "char_start": e.char_start} for e in j.evidence],
            } for j in res.judgements],
            "feedback": {
                "summary": res.feedback.summary if res.feedback else "",
                "suggestions": res.feedback.suggestions if res.feedback else [],
            },
        }
        out = os.path.join(CASES, rid + ".json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        nr = sum(1 for j in res.judgements if j.needs_review)
        ev = sum(1 for j in res.judgements if j.evidence)
        print(f"  -> {rid}.json  总分 {res.total}  "
              f"待复核 {nr}  带证据 {ev}/{len(res.judgements)}")
    print("完成：案例已写入 docs/cases/")


if __name__ == "__main__":
    main()
