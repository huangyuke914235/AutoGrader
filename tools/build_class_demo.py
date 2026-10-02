# -*- coding: utf-8 -*-
"""构建班级学情看板的演示数据（真实模型评阅，结果落盘后可随仓库发布）

    python tools/build_class_demo.py             # 真实模型评阅（需 LLM_API_KEY）
    python tools/build_class_demo.py --offline   # 离线规则引擎（零成本，但强度低）

做两件事：
1. 对 `samples_class/`（9 份典型形态的自造报告）+ 已有的 `data/demo/S0x.json`（3 份真实样本）
   跑完整评阅，产出 `data/class_demo/cohort.json`（batch_run 同款形状）；
2. 用它跑一次 classview 聚合，把**教师会看到的结论**打印出来供人工核对 ——
   看板的每条建议都是确定性算术推出来的，所以它必须能被人工验证。

为什么要固定 12 份：班级学情看板的意义在"看出共性短板"，
少于 8 份时比例完全没有意义（classview.MIN_N_FOR_INSIGHT 会把这一点说出来）。
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import classview as CV
import parser as P
from pipeline import default_rubric, run_offline_grading, run_grading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASS_SAMPLES = os.path.join(ROOT, "samples_class")
DEMO_DIR = os.path.join(ROOT, "data", "demo")
OUT_DIR = os.path.join(ROOT, "data", "class_demo")
OUT = os.path.join(OUT_DIR, "cohort.json")

COURSE = "Java程序设计 · 面向对象程序设计实验"


def _from_demo_results() -> list:
    """已有的 3 份真实样本结果，直接并入队列（它们已经评过，不重复花钱）"""
    recs = []
    if not os.path.isdir(DEMO_DIR):
        return recs
    for fn in sorted(os.listdir(DEMO_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(DEMO_DIR, fn), encoding="utf-8") as f:
            payload = json.load(f)
        meta = payload.get("_meta") or {}
        if (meta.get("engine") != "model"):
            # 规则引擎的产物不并入班级队列：混着两种引擎统计共性短板没有意义
            continue
        from models import GradingResult
        res = GradingResult.model_validate(payload["result"])
        rub = default_rubric()
        rec = CV.as_record(res.report_id, res, rub,
                           full_text=meta.get("full_text", ""),
                           name=res.report_id)
        recs.append(rec)
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="用离线规则引擎（零成本）")
    ap.add_argument("--no-recheck", action="store_true", help="关闭一致性复核（更快更省）")
    ap.add_argument("--force", action="store_true",
                    help="允许用不同引擎覆盖已有队列（会把对外公布的数字改掉）")
    args = ap.parse_args()

    # 与 build_demo.py 同一道保护：真实模型队列是有成本换来的，
    # 而且主页/PPT/README 引用的就是它的数字。离线模式重跑一次就会把它换掉，
    # 所有对外数字随之对不上 —— 那种不一致不报错，只是慢慢对不上，最难查。
    want = "rule" if args.offline else "model"
    if os.path.exists(OUT) and not args.force:
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                have = (json.load(f).get("engine") or "")
        except Exception:
            have = ""
        if have and have != want:
            print(f"已存在「{have}」引擎的队列 {os.path.relpath(OUT, ROOT)}，"
                  f"本次要用「{want}」——覆盖会改掉对外数字。")
            print("确认要覆盖请加 --force。")
            return 1

    rub = default_rubric()
    files = (sorted(f for f in os.listdir(CLASS_SAMPLES) if f.endswith(".txt"))
             if os.path.isdir(CLASS_SAMPLES) else [])
    if not files:
        print(f"缺少班级演示样本：先跑 python tools/make_class_roster.py")
        return 1

    records = _from_demo_results()
    print(f"并入已有真实样本 {len(records)} 份：" +
          "、".join(r["report_id"] for r in records) if records else "（无）")

    for i, fn in enumerate(files, 1):
        rid = os.path.splitext(fn)[0]
        path = os.path.join(CLASS_SAMPLES, fn)
        full, secs = P.parse_file(path)
        print(f"[{i}/{len(files)}] 评阅 {rid}（{len(full)} 字）…", flush=True)
        if args.offline:
            res = run_offline_grading(full, secs, rub.items, report_id=rid,
                                      course_hint=COURSE)
        else:
            res = run_grading(full, secs, "", report_id=rid, course_hint=COURSE,
                              rubric=rub, enable_recheck=not args.no_recheck)
        rec = CV.as_record(rid, res, rub, full_text=full, name=rid)
        records.append(rec)
        print(f"    -> {res.total} 分，"
              f"{sum(1 for d in rec['details'] if d['evidence'])}/"
              f"{len(rec['details'])} 项带证据")

    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {
        "version": "class-v1",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "course": COURSE,
        "engine": "rule" if args.offline else "model",
        "config": {"reports": len(records)},
        "reports": records,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {os.path.relpath(OUT, ROOT)}（{len(records)} 份）")

    # 立刻聚合一次并把教师会看到的结论打印出来 —— 看板是确定性算术，
    # 所以这些结论必须能被人工核对；打印出来是最省事的核对方式。
    agg = CV.aggregate(payload)
    ins = CV.build_insights(agg, CV.student_rows(payload), course=COURSE)
    print("\n" + "=" * 62)
    print(f"班级学情（n={agg['n_scored']}）：均分 {agg['totals']['mean']} "
          f"区间 {agg['totals']['min']}~{agg['totals']['max']} "
          f"及格率 {agg['totals']['pass_rate']}%")
    for it in agg["per_item"]:
        bar = "█" * int(round(it["weak_ratio"] * 20))
        print(f"  {it['name']:8s} 薄弱 {it['weak']}/{it['counted']} "
              f"{it['weak_ratio']:>5.0%} {bar}")
    print(f"待人工复核 {agg['needs_review_total']} 处")
    print("-" * 62)
    for a in ins["actions"]:
        print(f"[{a['type']}] {a['title']}")
        print(f"      依据：{a['detail']}")
    for nt in ins["notes"]:
        print(f"  注：{nt}")
    print("=" * 62)
    print("\n提示：这份数据供「班级学情」页做零配置演示。"
          "它含自造样本，公开前请确认不含真实学生信息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
