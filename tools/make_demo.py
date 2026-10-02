# -*- coding: utf-8 -*-
"""生成 GitHub Pages 主页用的离线案例 JSON

    source .venv/Scripts/activate
    python tools/make_demo.py            # 默认 S02 S03 S04
    python tools/make_demo.py S01 S05    # 指定报告

输出 **data/cases_full/S0x.json**（带全文的中间产物，已 gitignore）。

为什么不直接写 docs/cases/：
    那个目录是要 push 到公开仓库的。旧实现让本脚本直写 docs/cases/，
    裁剪只做一次（靠 `_is_public` 标记跳过），**一旦有人重跑一次、又没跑 publish_cases.py，
    作业全文就进公开仓库了**——没有任何自动闸门。
    现在：全量中间产物与公开产物彻底分开，公开目录只能由 publish_cases.py 写。
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as P
from models import Rubric
from pipeline import run_grading, fixed_rubric_items

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "data", "cases_full")       # 中间产物（gitignore）

#: 固定 5 项标准统一取自 pipeline（全项目唯一来源），本文件不再自存一份。
#: 原先这里有一份独立副本，信号词与 pipeline 的并不完全一致 ——
#: 而人工 gold 是按固定标准打的，两份并存会让"离线规则判定"与"对外基准口径"
#: 悄悄脱钩。详见 pipeline.DEFAULT_RUBRIC_ITEMS 的注释。
ITEMS = fixed_rubric_items()


def make_rubric():
    """每次返回全新对象：RubricItem 会被界面改脏，不能共用同一批实例"""
    return Rubric(items=fixed_rubric_items())


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
    print(f"完成：全量案例已写入 {os.path.relpath(CASES, ROOT)}/（该目录不进公开仓库）")
    print("要发布到主页（docs/cases/）请接着跑： python tools/publish_cases.py")


if __name__ == "__main__":
    main()
