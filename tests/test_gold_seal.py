# -*- coding: utf-8 -*-
"""gold 基准集的封存凭证测试

为什么要有这组测试：
    "非主程成员独立打分" 是整个自评测里最吃重的一句声明。
    这一组测试保证：只要本地存在 gold.json 与打分表，就必须能对上号——
    封存字段齐全、来源可追、且**表内分数与 gold.json 逐项一致**。
    （数据文件本身不进公开仓库，因此在没有数据的机器上自动跳过。）
"""
import os
import sys
import json

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GOLD = os.path.join(ROOT, "data", "gold", "gold.json")
XLSX = os.path.join(ROOT, "data", "gold", "gold_人工打分表_空白.xlsx")

pytestmark = pytest.mark.skipif(not os.path.exists(GOLD), reason="本机没有 gold.json")


def _sheet_rows():
    from openpyxl import load_workbook
    if not os.path.exists(XLSX):
        return None
    ws = load_workbook(XLSX, data_only=True)["③ 逐项打分"]
    rows = {}
    for r in range(2, ws.max_row + 1):
        rep, iid = ws.cell(r, 2).value, ws.cell(r, 3).value
        score, verdict = ws.cell(r, 6).value, ws.cell(r, 7).value
        if rep and iid:
            rows[(rep, iid)] = (float(score) if score not in (None, "") else None, verdict)
    return rows


def test_seal_fields_are_complete():
    g = json.load(open(GOLD, encoding="utf-8"))
    for k in ("scorer", "scorer_role", "scored_date", "independent", "no_ai_reference"):
        assert str(g.get(k) or "").strip(), f"封存字段 {k} 为空——独立性缺少凭证"
    assert g["independent"] == "是" and g["no_ai_reference"] == "是"
    assert g.get("source_sheet"), "没有记录基准集来自哪张打分表"
    assert "补填" in (g.get("seal_note") or "") or g.get("scored_date"), \
        "若封存字段是事后补填的，必须在 seal_note 里写明"


def test_gold_scores_match_source_sheet():
    """基准集必须与打分表逐项一致：这是"分数没被改过"的机器可验证版本"""
    rows = _sheet_rows()
    if rows is None:
        pytest.skip("本机没有打分表原件")
    g = json.load(open(GOLD, encoding="utf-8"))
    gold = {(rid, iid): (float(it["score"]), it["verdict"])
            for rid, rec in g["reports"].items() for iid, it in rec["items"].items()}
    diff = [k for k in set(rows) | set(gold) if rows.get(k) != gold.get(k)]
    assert not diff, f"打分表与 gold.json 不一致：{diff[:5]}"
    assert len(gold) == 50, f"基准集条目数异常：{len(gold)}"


def test_gold_totals_are_consistent():
    g = json.load(open(GOLD, encoding="utf-8"))
    for rid, rec in g["reports"].items():
        s = round(sum(float(v["score"]) for v in rec["items"].values()), 1)
        assert abs(s - float(rec["total"])) < 0.05, f"{rid} 总分与逐项之和不符"
