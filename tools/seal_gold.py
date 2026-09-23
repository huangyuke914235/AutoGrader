# -*- coding: utf-8 -*-
"""补录 gold set 的封存记录（当日的打分表原件已缺失时使用）

背景（别粉饰）：
    `data/gold/gold.json` 是 2026-09-21 由当时的打分表生成的，但**封存记录那几个字段是空的**：
    打分人、专业分工、打分日期、耗时全都没填，桌面只剩下空白模板，填写过的那份找不到了。
    而"非主程成员独立打分"是全篇最吃重的一句声明，没有凭证就会被合理质疑。

这个脚本做什么：
    1. 只写入封存字段，**绝不改动任何分数**——脚本会先算分数指纹，写完再验一次，不一致就回滚报错；
    2. 写入时自动带上 `seal_note`，明确标注"事后补录"，不允许伪装成当日填写；
    3. 同时在 `data/gold/` 生成一份可读的《封存记录.md》，把封存信息与分数表并列留档。

用法：
    python tools/seal_gold.py --scorer 张镒川 --role "产品与材料 / 非主程序成员" \\
        --scored-date 2026-09-21 --minutes 150 \\
        --seal-note "打分表原件已缺失，2026-09-23 依 gold.json 事后补录，分数一字未改"
"""
import os
import sys
import json
import hashlib
import argparse
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, "data", "gold", "gold.json")
RECORD = os.path.join(ROOT, "data", "gold", "封存记录.md")


def score_fingerprint(gold: dict) -> str:
    """只对分数做指纹：用于证明补录过程没有碰到任何分数"""
    items = []
    for rid in sorted(gold.get("reports", {})):
        rec = gold["reports"][rid]
        items.append(rid + ":" + str(rec.get("total")))
        for iid in sorted(rec.get("items", {})):
            items.append(f"{rid}/{iid}={rec['items'][iid].get('score')}")
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scorer", required=True, help="打分人姓名")
    ap.add_argument("--role", required=True, help="专业 / 分工")
    ap.add_argument("--scored-date", required=True, help="打分日期（当日实际日期）")
    ap.add_argument("--minutes", default="", help="总耗时（分钟）")
    ap.add_argument("--independent", default="是", help="是否独立完成（未与主程讨论）")
    ap.add_argument("--no-ai-reference", default="是", help="是否全程未参考 AI 评分")
    ap.add_argument("--seal-note", required=True, help="补录说明，如实写明为何是事后补录")
    args = ap.parse_args()

    if not os.path.exists(GOLD):
        print(f"找不到 {GOLD}，先跑 tools/import_gold.py")
        sys.exit(1)

    gold = json.load(open(GOLD, encoding="utf-8"))
    before = score_fingerprint(gold)

    gold["scorer"] = args.scorer
    gold["scorer_role"] = args.role
    gold["scored_date"] = args.scored_date
    gold["minutes_spent"] = args.minutes
    gold["independent"] = args.independent
    gold["no_ai_reference"] = args.no_ai_reference
    gold["seal_note"] = args.seal_note
    gold["seal_filled_at"] = datetime.datetime.now().isoformat(timespec="seconds")

    after = score_fingerprint(gold)
    if before != after:
        print("❌ 分数指纹发生变化，已中止且未写入。请检查脚本。")
        sys.exit(1)

    json.dump(gold, open(GOLD, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    lines = [
        "# gold set 封存记录（补录）",
        "",
        f"- 补录时间：{gold['seal_filled_at']}",
        f"- 打分人：{args.scorer}（{args.role}）",
        f"- 打分日期：{args.scored_date}",
        f"- 耗时：{args.minutes or '未记录'} 分钟",
        f"- 独立完成（未与主程讨论）：{args.independent}",
        f"- 全程未参考 AI 评分：{args.no_ai_reference}",
        f"- **补录说明**：{args.seal_note}",
        f"- 分数指纹（补录前后一致）：`{after}`",
        "",
        "## 分数明细（gold.json 原样抄录，未做任何修改）",
        "",
        "| 报告 | 总分 |",
        "|---|---|",
    ]
    for rid in sorted(gold.get("reports", {})):
        lines.append(f"| {rid} | {gold['reports'][rid].get('total')} |")
    lines += ["", "| 报告 | 评分点 | 得分 | 判定 |", "|---|---|---|---|"]
    for rid in sorted(gold.get("reports", {})):
        for iid, it in sorted(gold["reports"][rid].get("items", {}).items()):
            lines.append(f"| {rid} | {iid} | {it.get('score')} | {it.get('verdict','')} |")
    open(RECORD, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print(f"✅ 已补录封存记录 -> {GOLD}")
    print(f"✅ 已生成可读留档 -> {RECORD}")
    print(f"分数指纹：{before}（补录前后一致，分数一字未改）")
    print("注意：seal_note 已明确标注为事后补录，答辩时可据此如实说明。")


if __name__ == "__main__":
    main()
