# -*- coding: utf-8 -*-
"""班级学情聚合与教学建议的测试

这个模块是「AI + 教学管理助手」里"管理"那一半，结论会直接影响教师怎么排课，
所以它的两条纪律必须有测试守着：
1. **系统的问题不能算到学生头上** —— 调用失败、召回模式强制转人工的 miss，
   都不能进"学生没做到"的统计；否则教师会去讲一个其实没问题的知识点。
2. **样本不足要说样本不足** —— n 小的时候不许下"这个班普遍如何"的结论。
"""
import classview as CV


def _detail(id_, name, verdict, s, max_, review=False, err="", ev=None):
    return {"id": id_, "name": name, "verdict": verdict, "s": s, "max": max_,
            "confidence": 0.9, "needs_review": review, "system_error": err,
            "evidence": ev if ev is not None else (["证据原文"] if verdict != "miss" else []),
            "dropped": []}


def _report(rid, total, details, incomplete=False, coverage="full", name=""):
    return {"report_id": rid, "total": total, "name": name or rid,
            "details": details, "total_incomplete": incomplete, "coverage": coverage}


ITEMS = [("r1", "实验目的明确", 15.0), ("r2", "环境与步骤", 20.0),
         ("r3", "核心实现", 25.0), ("r4", "结果与数据", 20.0),
         ("r5", "分析与总结", 20.0)]


def _batch(verdicts_per_report):
    """构造一批结果：verdicts_per_report = [[每项的 (verdict, score)], ...]"""
    rs = []
    for i, vs in enumerate(verdicts_per_report, 1):
        ds = []
        for (iid, name, mx), (v, s) in zip(ITEMS, vs):
            ds.append(_detail(iid, name, v, s, mx))
        rs.append(_report(f"S{i:02d}", round(sum(x[1] for x in vs), 1), ds))
    return {"reports": rs}


# ---------- 聚合 ----------

def test_aggregate_counts_verdicts_and_ratios():
    batch = _batch([
        [("hit", 15.0), ("hit", 20.0), ("miss", 0.0), ("hit", 20.0), ("partial", 10.0)],
        [("hit", 15.0), ("partial", 13.0), ("miss", 0.0), ("hit", 20.0), ("hit", 20.0)],
        [("miss", 0.0), ("hit", 20.0), ("partial", 15.0), ("hit", 20.0), ("hit", 20.0)],
        [("hit", 15.0), ("hit", 20.0), ("miss", 0.0), ("partial", 12.0), ("hit", 20.0)],
    ])
    agg = CV.aggregate(batch)
    assert agg["n_reports"] == 4
    assert agg["n_scored"] == 4

    r3 = next(i for i in agg["per_item"] if i["id"] == "r3")
    assert (r3["hit"], r3["partial"], r3["miss"]) == (0, 1, 3)
    assert r3["weak"] == 4 and r3["weak_ratio"] == 1.0     # 全班都栽在核心实现
    assert r3["miss_ratio"] == 0.75
    assert r3["avg_score"] == round((0 + 0 + 15 + 0) / 4, 2)

    r1 = next(i for i in agg["per_item"] if i["id"] == "r1")
    assert r1["hit"] == 3 and r1["miss"] == 1 and r1["weak_ratio"] == 0.25


def test_aggregate_distribution_and_rates():
    batch = _batch([
        [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("hit", 20.0), ("hit", 20.0)],   # 100
        [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("hit", 20.0), ("partial", 10.0)],  # 90
        [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("partial", 12.0), ("partial", 10.0)],  # 82
        [("hit", 15.0), ("partial", 13.0), ("partial", 15.0), ("partial", 10.0), ("miss", 0.0)],  # 53
    ])
    agg = CV.aggregate(batch)
    assert agg["totals"]["max"] == 100.0
    assert agg["totals"]["min"] == 53.0
    assert agg["totals"]["range"] == 47.0
    # 及格 3/4，优秀（≥85）2/4
    assert agg["totals"]["pass_rate"] == 75.0
    assert agg["totals"]["excellent_rate"] == 50.0
    dist = {b["label"]: b["count"] for b in agg["distribution"]}
    assert dist["<60"] == 1 and dist["90-100"] == 2 and dist["80-90"] == 1


# ---------- 纪律一：系统的问题不算到学生头上 ----------

def test_system_errors_are_excluded_from_statistics():
    """模型调用失败的那一项不参与"学生是否做到"的统计，也不进分数分布"""
    good = [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("hit", 20.0), ("hit", 20.0)]
    batch = _batch([good, good])
    # 第三份：核心实现那次调用失败（system_error），且总分标为不完整
    ds = [_detail(i, n, "miss" if i == "r3" else "hit",
                  0.0 if i == "r3" else mx, mx,
                  err="模型调用失败" if i == "r3" else "")
          for i, n, mx in ITEMS]
    batch["reports"].append(_report("S03", 80.0, ds, incomplete=True))

    agg = CV.aggregate(batch)
    r3 = next(i for i in agg["per_item"] if i["id"] == "r3")
    assert r3["counted"] == 2, "系统错误的那一项不该被计入"
    assert r3["miss"] == 0, "调用失败不能被算成学生没做到"
    # 不完整的分数不进分布与均值
    assert agg["n_scored"] == 2 and agg["n_incomplete"] == 1
    assert agg["totals"]["min"] == 100.0


def test_retrieved_mode_is_reported_as_quality_note():
    batch = _batch([
        [("hit", 15.0)] * 5,
        [("hit", 15.0)] * 5,
    ])
    batch["reports"][1]["coverage"] = "retrieved"
    agg = CV.aggregate(batch)
    assert agg["n_retrieved_mode"] == 1


def test_needs_review_is_counted_per_item():
    batch = _batch([
        [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("hit", 20.0), ("hit", 20.0)],
    ])
    batch["reports"][0]["details"][2]["needs_review"] = True
    agg = CV.aggregate(batch)
    assert agg["needs_review_total"] == 1


# ---------- 纪律二：样本不足要说样本不足 ----------

def test_small_sample_is_flagged_and_no_strong_claim():
    batch = _batch([
        [("miss", 0.0)] * 5,
        [("miss", 0.0)] * 5,
    ])
    agg = CV.aggregate(batch)
    ins = CV.build_insights(agg)
    assert ins["ok"] is True
    assert any("样本偏小" in x for x in ins["notes"]), "小样本必须明确提示"
    # 仍然给出建议，但已附带样本量说明
    assert ins["actions"]


def test_empty_batch_returns_clear_reason_not_crash():
    ins = CV.build_insights(CV.aggregate({"reports": []}))
    assert ins["ok"] is False
    assert ins["reason"]
    assert ins["actions"] == []


# ---------- 建议必须可指认 ----------

def test_weak_item_becomes_teaching_action_with_evidence():
    batch = _batch([
        [("hit", 15.0), ("hit", 20.0), ("miss", 0.0), ("hit", 20.0), ("hit", 20.0)],
        [("hit", 15.0), ("hit", 20.0), ("partial", 10.0), ("hit", 20.0), ("hit", 20.0)],
        [("hit", 15.0), ("hit", 20.0), ("miss", 0.0), ("hit", 20.0), ("hit", 20.0)],
    ])
    agg = CV.aggregate(batch)
    ins = CV.build_insights(agg)
    teach = [a for a in ins["actions"] if a["type"] == "teaching"]
    assert teach, "超过半数没拿满的评分点必须给出教学建议"
    a = teach[0]
    assert "核心实现" in a["title"]
    # 规模小的时候不看比例下结论，但建议本身仍要带可指认的证据
    assert a["evidence"]["item_id"] == "r3"
    assert a["evidence"]["weak"] >= 2
    assert a["suggest"], "建议不能为空"


def test_all_hit_item_is_flagged_as_no_discrimination():
    """全班都拿满的评分点要提醒：它没有区分度，可能是标准太松"""
    good = [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("hit", 20.0), ("hit", 20.0)]
    batch = _batch([good, good, good, good])
    agg = CV.aggregate(batch)
    ins = CV.build_insights(agg)
    rubric_actions = [a for a in ins["actions"] if a["type"] == "rubric"]
    assert len(rubric_actions) == len(ITEMS), "五项全满，五项都该被提示无区分度"
    assert all("没有区分度" in a["title"] for a in rubric_actions)


def test_missing_item_suggests_clarifying_requirement():
    """整项缺失更像"要求没说清"，建议应当是补作业说明而不是"加强练习" """
    miss = [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("hit", 20.0), ("miss", 0.0)]
    batch = _batch([miss, miss, miss])
    agg = CV.aggregate(batch)
    ins = CV.build_insights(agg)
    brief = [a for a in ins["actions"] if a["type"] == "brief"]
    assert brief
    assert "要求没落到纸面" in brief[0]["detail"]
    assert "作业说明" in brief[0]["suggest"]


def test_review_pending_becomes_action():
    batch = _batch([
        [("hit", 15.0), ("hit", 20.0), ("partial", 10.0), ("hit", 20.0), ("hit", 20.0)],
    ])
    batch["reports"][0]["details"][2]["needs_review"] = True
    ins = CV.build_insights(CV.aggregate(batch))
    review = [a for a in ins["actions"] if a["type"] == "review"]
    assert review and "人工复核" in review[0]["title"]
    assert "不确定性" in review[0]["detail"]


def test_quality_notes_mention_incomplete_and_retrieved():
    batch = _batch([
        [("hit", 15.0)] * 5,
        [("hit", 15.0)] * 5,
    ])
    batch["reports"][0]["total_incomplete"] = True
    batch["reports"][1]["coverage"] = "retrieved"
    ins = CV.build_insights(CV.aggregate(batch))
    joined = " ".join(ins["notes"])
    assert "不完整" in joined
    assert "召回" in joined


# ---------- 学生名单 ----------

def test_student_rows_sorted_by_total_and_carry_weakest():
    batch = _batch([
        [("hit", 15.0), ("hit", 20.0), ("hit", 25.0), ("hit", 20.0), ("hit", 20.0)],   # 100
        [("miss", 0.0), ("partial", 13.0), ("partial", 15.0), ("partial", 10.0), ("miss", 0.0)],  # 38
        [("hit", 15.0), ("hit", 20.0), ("partial", 15.0), ("hit", 20.0), ("partial", 10.0)],  # 80
    ])
    rows = CV.student_rows(batch)
    assert [r["total"] for r in rows] == [38.0, 80.0, 100.0], "应当按总分升序，教师先看最差的"
    low = rows[0]
    assert low["miss_count"] == 2
    assert low["weakest"], "最弱项必须给出"
    assert low["weakest_gap"] > 0


def test_student_rows_tolerate_missing_total():
    batch = _batch([[("hit", 15.0)] * 5])
    batch["reports"][0]["total"] = None
    rows = CV.student_rows(batch)
    assert len(rows) == 1 and rows[0]["total"] is None


# ---------- 与 batch_run 的实际产物对接 ----------

def test_accepts_batch_run_shape():
    """tools/batch_run.py 的产物把明细放在 details 之外 —— 两种形状都要能读"""
    payload = {"version": "v2", "config": {}, "results": [
        _report("S01", 88.0, [_detail(i, n, "hit", mx, mx) for i, n, mx in ITEMS]),
    ]}
    agg = CV.aggregate(payload)
    assert agg["n_reports"] == 1 and agg["n_scored"] == 1
    assert agg["totals"]["mean"] == 88.0


def test_accepts_string_max_score():
    """界面导出的 CSV/JSON 里 max 可能是字符串，聚合不能因此炸掉"""
    batch = {"reports": [{"report_id": "S1", "total": 10.0, "details": [
        {"id": "r1", "name": "目的", "verdict": "partial", "s": "5", "max": "15",
         "needs_review": False, "system_error": "", "evidence": ["x"], "dropped": []}]}]}
    agg = CV.aggregate(batch)
    assert agg["n_scored"] == 1
    assert agg["per_item"][0]["avg_score"] == 5.0


# ---------- 与 GradingResult 的桥接契约 ----------

def _fake_result():
    from models import (GradingResult, Rubric, RubricItem, ItemJudgement,
                        Evidence, RunInfo)

    class _Res:
        pass
    items = [RubricItem(id=i, name=n, criteria="c", max_score=mx)
             for i, n, mx in ITEMS]
    js = [ItemJudgement(rubric_item_id="r1", verdict="hit", score=15.0, confidence=0.9,
                        reason="r", evidence=[Evidence(section_id="", quote="证据",
                                                       char_start=0)]),
          ItemJudgement(rubric_item_id="r2", verdict="miss", score=0.0, confidence=0.9,
                        reason="r"),
          ItemJudgement(rubric_item_id="r3", verdict="partial", score=10.0, confidence=0.4,
                        reason="r", needs_review=True,
                        evidence=[Evidence(section_id="", quote="证据2", char_start=0)],
                        dropped_quotes=["被剔除的引用"])]
    res = _Res()
    res.judgements = js
    res.total = 25.0
    res.total_incomplete = False
    res.run_info = RunInfo(parse_coverage="full")
    return res, Rubric(items=items)


def test_as_record_produces_the_shape_aggregate_expects():
    """这是跨模块契约：batch_ui / CLI / 演示三处都从这里转，形状必须能直接喂给 aggregate"""
    res, rub = _fake_result()
    rec = CV.as_record("S01", res, rub, full_text="正文若干", name="张三")

    assert rec["report_id"] == "S01" and rec["name"] == "张三"
    assert rec["chars"] == 4 and rec["coverage"] == "full"
    by_id = {d["id"]: d for d in rec["details"]}
    # name / max 必须从 rubric 回填：否则看板只能显示 r1/r2 这种内部 id
    assert by_id["r1"]["name"] == "实验目的明确"
    assert by_id["r3"]["max"] == 25.0
    assert by_id["r3"]["needs_review"] is True
    assert by_id["r3"]["dropped"] == ["被剔除的引用"]

    # 直接喂给 aggregate，应当得到与手工构造一致的结果
    agg = CV.aggregate(CV.as_batch([rec], course="Java程序设计"))
    assert agg["n_scored"] == 1
    assert agg["needs_review_total"] == 1
    assert next(i for i in agg["per_item"] if i["id"] == "r3")["partial"] == 1


def test_as_record_survives_missing_run_info():
    """评审结果没有 run_info 时不能炸（预置演示结果里就可能是空的）"""
    res, rub = _fake_result()
    res.run_info = None
    rec = CV.as_record("S02", res, rub, full_text="x")
    assert rec["coverage"] == ""
    assert CV.aggregate(CV.as_batch([rec]))["n_scored"] == 1


def test_scored_total_matches_between_row_and_aggregate():
    """界面行里的总分与看板统计的总分必须来自同一处 —— 不一致比数字难看严重"""
    res, rub = _fake_result()
    rec = CV.as_record("S03", res, rub, full_text="x")
    agg = CV.aggregate(CV.as_batch([rec]))
    assert agg["totals"]["mean"] == rec["total"]
