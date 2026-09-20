# -*- coding: utf-8 -*-
"""把 B 成员填好的打分表转成 gold.json（封存）

为什么要有这一步：
    人工分数留在 Excel 里没法被程序读取，也没法「封存」。
    转成的 gold.json 是 D6 benchmark 的唯一标准答案，生成后即视为冻结：
    任何修改都必须重新打分并重新生成，不允许手改 json。

用法：
    python tools/import_gold.py

校验规则：
    - 50 行必须全部填了得分，缺一个就拒绝生成（防止半份 gold 混进评测）
    - 每项得分必须在 0 ~ 该项满分之间
    - 判定必须是 hit / partial / miss
"""
import os
import sys
import json
import datetime

try:
    from openpyxl import load_workbook
except ImportError:
    print("缺少 openpyxl，请先安装： pip install openpyxl")
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "data", "gold", "gold_人工打分表_空白.xlsx")
OUT = os.path.join(ROOT, "data", "gold", "gold.json")

MAXSCORE = {"r1": 15, "r2": 20, "r3": 25, "r4": 20, "r5": 20}
VERDICTS = {"hit", "partial", "miss"}


def cell(ws, row, col):
    v = ws.cell(row=row, column=col).value
    if isinstance(v, str):
        v = v.strip()
    return v


def read_seal(wb):
    if "⑤ 封存记录" not in wb.sheetnames:
        return {}
    ws = wb["⑤ 封存记录"]
    info = {}
    for r in range(3, 12):
        k = cell(ws, r, 1)
        v = cell(ws, r, 2)
        if k:
            info[str(k)] = v
    return info


def main():
    if not os.path.exists(XLSX):
        print(f"找不到打分表：{XLSX}")
        sys.exit(1)

    wb = load_workbook(XLSX, data_only=True)   # data_only：读公式的计算结果
    ws = wb["③ 逐项打分"]

    gold = {}
    problems = []
    filled = 0
    expected = 0

    for r in range(2, ws.max_row + 1):
        rep = cell(ws, r, 2)
        iid = cell(ws, r, 3)
        if not rep or not iid:
            continue
        expected += 1
        mx = MAXSCORE.get(iid)
        score = cell(ws, r, 6)
        verdict = cell(ws, r, 7)
        evidence = cell(ws, r, 8)
        note = cell(ws, r, 9)

        if score is None or score == "":
            problems.append(f"第{r}行 {rep}/{iid}：得分未填")
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            problems.append(f"第{r}行 {rep}/{iid}：得分不是数字（{score!r}）")
            continue
        if not (0 <= score <= mx):
            problems.append(f"第{r}行 {rep}/{iid}：得分 {score} 超出 0~{mx}")
            continue
        if verdict and verdict not in VERDICTS:
            problems.append(f"第{r}行 {rep}/{iid}：判定 {verdict!r} 不合法")
            continue

        filled += 1
        gold.setdefault(rep, {"items": {}, "total": 0.0})
        gold[rep]["items"][iid] = {
            "score": score,
            "verdict": verdict or "",
            "evidence": evidence or "",
            "note": note or "",
        }
        gold[rep]["total"] += score

    print(f"已填写 {filled} / {expected} 项")
    if problems:
        print(f"\n发现 {len(problems)} 个问题，未生成 gold.json：")
        for p in problems[:20]:
            print("  -", p)
        if len(problems) > 20:
            print(f"  …… 还有 {len(problems)-20} 条")
        sys.exit(1)
    if filled < expected:
        print(f"还有 {expected-filled} 项没填完，拒绝生成（半份 gold 会让评测失真）")
        sys.exit(1)

    seal = read_seal(wb)
    data = {
        "_note": "人工 gold set，非主程成员独立打分，生成后封存，禁止手改",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "scorer": seal.get("打分人姓名") or "",
        "scorer_role": seal.get("专业 / 分工") or "",
        "scored_date": seal.get("打分日期") or "",
        "minutes_spent": seal.get("总耗时（分钟）") or "",
        "independent": seal.get("是否独立完成（未与主程讨论）") or "",
        "no_ai_reference": seal.get("是否全程未参考 AI 评分") or "",
        "rubric": MAXSCORE,
        "reports": gold,
    }
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    totals = {k: round(v["total"], 1) for k, v in sorted(gold.items())}
    print(f"\n✅ 已生成 {OUT}")
    print("各报告人工总分：")
    for k, v in totals.items():
        print(f"   {k}: {v}")
    vals = list(totals.values())
    print(f"\n最高 {max(vals)} 最低 {min(vals)} 极差 {max(vals)-min(vals)}")
    if seal.get("是否独立完成（未与主程讨论）") != "是":
        print("\n⚠ 封存记录里「是否独立完成」不是「是」——这份 gold set 的公信力会受质疑，请确认。")


if __name__ == "__main__":
    main()
