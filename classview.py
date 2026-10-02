# -*- coding: utf-8 -*-
"""班级学情：把逐份的评阅结果聚合成**教师能直接用来调整教学**的东西

这个模块补的是项目定位里最要命的一个缺口。作品原来只做「一份报告的评阅」，
而赛题方向是「AI + 教学管理助手」—— 管理真正要回答的不是"这份报告几分"，
而是"这个班普遍卡在哪、我下一节课该讲什么"。

设计纪律（与评分链路同源，都是"结论必须能被指认"）：

1. **聚合是纯确定性的**：判定来自模型，但"多少人栽在同一项"是算术，
   由代码算，不给模型任何解释空间。
2. **系统错误与待复核不参与失分统计**：调用失败、低置信、两次判定不一致
   都不能算成"学生没做到"—— 那会把系统的问题记到学生头上，
   也会让教师去讲一个其实没问题的知识点。
3. **建议必须可指认**：每条干预建议都要说清它是由哪几项数据推出来的
   （哪些学生、哪一项、什么形态），不接受"建议加强练习"这种正确的废话。
4. **样本不足就说样本不足**：n 小的时候不下强结论，界面与建议里都要写明。

数据契约：接受 `tools/batch_run.py` 产出的明细 dict（这也是界面批量测试的格式），
不依赖 GradingResult 对象 —— 让同一份数据在 CLI、界面、演示里只走一条路。
"""
from typing import Dict, List, Optional

#: 一个评分点被判为「明显缺失」的判定集合
WEAK_VERDICTS = ("miss", "partial")

#: 低于这个参评数就不下强结论（样本太小时任何比例都不可靠）
MIN_N_FOR_INSIGHT = 8

#: 单点薄弱率超过这个比例，才认为"这个班普遍卡在这里"
WEAK_RATIO_ALERT = 0.5


def _judgement_of(detail: dict, item_id: str) -> Optional[dict]:
    for d in detail.get("details") or []:
        if d.get("id") == item_id:
            return d
    return None


def _is_countable(d: dict) -> bool:
    """这条判定能不能参与「学生是否做到」的统计。

    不能算进来的三类，理由各不相同，但共同点是**责任不在学生**：
    - system_error：模型调用失败，等于没判；
    - 召回模式下强制转人工的 miss：只看到部分正文时"找不到"不代表没写；
    - 无证据的空判定：连依据都没有，不构成结论。
    低置信 / 两次不一致（needs_review）照旧计入，但会单独计数提醒教师复核。
    """
    if d.get("system_error"):
        return False
    if d.get("verdict") == "miss" and not d.get("evidence"):
        # miss 允许无证据（本来就该没有），但不能是"系统没判"——由 system_error 拦住
        return True
    return True


def aggregate(batch: dict) -> dict:
    """把一批评阅结果聚合成班级学情。

    入参 batch：{"details": [ {report_id, total, details:[{id,name,verdict,s,confidence,
                needs_review,system_error,evidence,dropped}], coverage, total_incomplete}, ... ]}
                或直接给 reports 列表。

    返回一个**纯数据**结构（不含任何文案判断），由 build_insights 负责解读。
    """
    reports = batch.get("reports") or batch.get("results") or []
    if not reports and isinstance(batch.get("details"), list):
        reports = batch["details"]

    # 评分点顺序与满分从第一条有细节的记录里取；全都没有就返回空
    items: List[dict] = []
    for r in reports:
        for d in r.get("details") or []:
            if d.get("id") and not any(i["id"] == d["id"] for i in items):
                items.append({"id": d["id"], "name": d.get("name") or d["id"]})

    per_item = []
    for it in items:
        hit = partial = miss = 0
        counted = 0
        scores, maxes = [], []
        review = 0
        for r in reports:
            d = _judgement_of(r, it["id"])
            if not d or not _is_countable(d):
                continue
            counted += 1
            v = d.get("verdict")
            if v == "hit":
                hit += 1
            elif v == "partial":
                partial += 1
            else:
                miss += 1
            if d.get("needs_review"):
                review += 1
            if d.get("max") is not None:
                scores.append(float(d.get("s") or 0))
                maxes.append(float(d["max"]))
        weak = partial + miss
        per_item.append({
            "id": it["id"], "name": it["name"],
            "counted": counted, "hit": hit, "partial": partial, "miss": miss,
            "weak": weak,
            "weak_ratio": round(weak / counted, 3) if counted else 0.0,
            "miss_ratio": round(miss / counted, 3) if counted else 0.0,
            "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
            "max_score": maxes[0] if maxes else None,
            "avg_ratio": (round(sum(scores) / sum(maxes), 3)
                          if maxes and sum(maxes) > 0 else None),
            "needs_review": review,
        })

    totals = [float(r.get("total") or 0) for r in reports
              if not r.get("total_incomplete")]
    dist = _histogram(totals)
    n_incomplete = sum(1 for r in reports if r.get("total_incomplete"))
    n_retrieved = sum(1 for r in reports if r.get("coverage") == "retrieved")

    return {
        "n_reports": len(reports),
        "n_scored": len(totals),
        "n_incomplete": n_incomplete,
        "n_retrieved_mode": n_retrieved,
        "per_item": per_item,
        "totals": {"n": len(totals),
                   "mean": round(sum(totals) / len(totals), 1) if totals else 0.0,
                   "max": round(max(totals), 1) if totals else 0.0,
                   "min": round(min(totals), 1) if totals else 0.0,
                   "range": round(max(totals) - min(totals), 1) if totals else 0.0,
                   "pass_rate": (round(100.0 * sum(1 for t in totals if t >= 60)
                                       / len(totals), 1) if totals else 0.0),
                   "excellent_rate": (round(100.0 * sum(1 for t in totals if t >= 85)
                                            / len(totals), 1) if totals else 0.0)},
        "distribution": dist,
        "needs_review_total": sum(i["needs_review"] for i in per_item),
    }


def _histogram(totals, bins=(0, 60, 70, 80, 90, 100)):
    """分数分布：不及格 / 60-69 / 70-79 / 80-89 / 90-100"""
    labels = []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        labels.append((f"{lo}-{hi}" if i else f"<{hi}", lo, hi))
    out = [{"label": lb, "count": 0} for lb, _, _ in labels]
    for t in totals:
        for idx, (_, lo, hi) in enumerate(labels):
            if t < hi or idx == len(labels) - 1:
                out[idx]["count"] += 1
                break
    return out


def student_rows(batch: dict) -> List[dict]:
    """每个学生一行：总分 + 最弱的一项 + 是否待复核/不完整。

    教师最先要的是"谁需要我找一趟"，而不是一张分数矩阵。
    """
    reports = batch.get("reports") or batch.get("details") or []
    rows = []
    for r in reports:
        ds = [d for d in (r.get("details") or []) if _is_countable(d)]
        weak = [d for d in ds if d.get("verdict") in WEAK_VERDICTS]
        weak.sort(key=lambda d: (d.get("s") or 0) / max(1e-9, float(d.get("max") or 1)))
        rows.append({
            "report_id": r.get("report_id", ""),
            "name": r.get("name") or r.get("report_id", ""),
            "total": r.get("total"),
            "incomplete": bool(r.get("total_incomplete")),
            "coverage": r.get("coverage", ""),
            "needs_review": sum(1 for d in ds if d.get("needs_review")),
            "weakest": (weak[0].get("name") or weak[0].get("id")) if weak else "",
            "weakest_gap": (round(float(weak[0].get("max") or 0) - float(weak[0].get("s") or 0), 1)
                            if weak else 0.0),
            "miss_count": sum(1 for d in ds if d.get("verdict") == "miss"),
        })
    rows.sort(key=lambda x: (x["total"] is None, x["total"] if x["total"] is not None else 999))
    return rows


def build_insights(agg: dict, per_student: Optional[List[dict]] = None,
                   course: str = "") -> dict:
    """从聚合结果推出**可执行的**教学建议。

    每条建议都带 evidence 字段，写清它由哪几项数据推出来的。
    宁可不给建议（说"样本不足"），也不给"建议加强练习"这种正确但没用的废话。
    """
    n = agg.get("n_scored") or 0
    items = agg.get("per_item") or []
    notes, actions = [], []

    if n == 0:
        return {"ok": False, "reason": "没有可用结果（可能全部因系统错误未判定）",
                "notes": [], "actions": []}

    if n < MIN_N_FOR_INSIGHT:
        notes.append(f"本次只有 {n} 份有效结果，**样本偏小**："
                     f"下面的比例只作参考，不要据此下「这个班普遍如何」的结论。"
                     f"（建议 ≥{MIN_N_FOR_INSIGHT} 份再看比例）")

    # 1) 普遍薄弱的评分点：低于一半学生拿满，就值得占用课堂时间
    weak_items = [i for i in items if i["counted"] and i["weak_ratio"] >= WEAK_RATIO_ALERT]
    weak_items.sort(key=lambda i: -i["weak_ratio"])
    for i in weak_items:
        detail = (f"{i['weak']}/{i['counted']} 份没拿满"
                  f"（完全缺失 {i['miss']} 份）")
        if i["avg_score"] is not None and i["max_score"]:
            detail += f"，该项均分 {i['avg_score']}/{i['max_score']}"
        actions.append({
            "type": "teaching",
            "title": f"「{i['name']}」是本次的共性短板",
            "detail": detail,
            "evidence": {"item_id": i["id"], "weak": i["weak"],
                         "weak_ratio": i["weak_ratio"], "miss": i["miss"],
                         "counted": i["counted"],
                         "avg_score": i["avg_score"], "max_score": i["max_score"]},
            "suggest": _suggest_for_item(i),
        })

    # 2) 集中缺失（多数人整项没写）：通常意味着要求没说清或没给模板
    missing = [i for i in items if i["counted"] and i["miss_ratio"] >= WEAK_RATIO_ALERT]
    for i in missing:
        actions.append({
            "type": "brief",
            "title": f"「{i['name']}」有 {i['miss']} 份完全没写",
            "detail": "整项缺失通常不是学生偷懒，而是**要求没落到纸面**："
                      "作业说明里有没有明确要求这一节？有没有给过范例？",
            "evidence": {"item_id": i["id"], "miss_ratio": i["miss_ratio"],
                         "miss": i["miss"], "counted": i["counted"]},
            "suggest": f"在作业说明里把「{i['name']}」写成一条可勾选的要求，"
                       f"并附一份达标的范例；下一轮再统计这一项是否改善。",
        })

    # 3) 疑似评分标准本身有问题：全班都拿满，说明这一项没有区分度
    ceiling = [i for i in items if i["counted"] >= max(3, n // 2)
               and i["hit"] == i["counted"] and i["counted"] > 0]
    for i in ceiling:
        actions.append({
            "type": "rubric",
            "title": f"「{i['name']}」全班都拿满，这一项没有区分度",
            "detail": f"{i['hit']}/{i['counted']} 份都是满分。"
                      f"要么这一项太容易，要么判定标准没有真正区分优劣。",
            "evidence": {"item_id": i["id"], "hit": i["hit"], "counted": i["counted"]},
            "suggest": "把该项的判定标准写细（例如从「有没有写」改成"
                       "「是否给出可复现的步骤或数据支撑」），或降低它的分值权重，"
                       "把分留给能区分的维度。",
        })

    # 4) 需要教师人工过一遍的量（系统不确定性，不是学生问题）
    review = agg.get("needs_review_total") or 0
    if review:
        actions.append({
            "type": "review",
            "title": f"有 {review} 处判定等待人工复核",
            "detail": "这些是低置信、两次判定不一致或分差过大的项 —— "
                      "属于**系统的不确定性**，不能直接当成绩发布。",
            "evidence": {"needs_review_total": review},
            "suggest": "在「② 详情对照」里逐条确认；确认后分数才可作为初评交给学生。",
        })

    # 5) 数据质量问题必须在结论之前说清楚
    if agg.get("n_incomplete"):
        notes.append(f"{agg['n_incomplete']} 份含系统错误、总分不完整，"
                     f"**未计入**上面的分数分布与均值。")
    if agg.get("n_retrieved_mode"):
        notes.append(f"{agg['n_retrieved_mode']} 份正文超长走了关键词召回，"
                     f"其结果可信度低于全文模式（相关 miss 已被强制转人工）。")

    # 6) 需要关注的学生（仅在有名单时给）
    focus = []
    if per_student:
        low = [s for s in per_student if (s["total"] is not None and s["total"] < 60)]
        for s in low[:8]:
            focus.append({"name": s["name"], "total": s["total"],
                          "weakest": s["weakest"], "gap": s["weakest_gap"]})

    return {
        "ok": True,
        "course": course,
        "n": n,
        "notes": notes,
        "actions": actions,
        "focus_students": focus,
        "exemplars": exemplars(per_student),
    }


def exemplars(per_student: Optional[List[dict]], top_n: int = 3) -> List[dict]:
    """可以当范例讲的学生。

    为什么和"需要关注的学生"一样重要：教师调整教学不只需要知道谁落后，
    还需要**手上有一份可以直接投影出来的达标样本** ——
    "把「分析与总结」讲一次，用一份达标样本与一份不达标样本做对照"
    这条建议如果没有现成样本，教师还得自己去找。所以这里直接给出来。
    """
    if not per_student:
        return []
    good = [s for s in per_student
            if s.get("total") is not None and not s.get("incomplete")
            and s.get("miss_count", 0) == 0 and s.get("needs_review", 0) == 0]
    good.sort(key=lambda s: -float(s["total"]))
    return [{"name": s["name"], "total": s["total"]} for s in good[:top_n]]


def _suggest_for_item(item: dict) -> str:
    """给一个薄弱评分点配一条**可执行**的课堂动作（不是"建议加强练习"）"""
    return (f"把「{item['name']}」拎出来讲一次，用一份达标样本与一份不达标样本做对照；"
            f"讲完在作业说明里补一条对应的自查项，让下一轮可验证是否改善。")


def load_batch_file(path: str) -> dict:
    """读 batch_run / 界面导出的批量结果 JSON"""
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- 与评阅结果的桥接 ----------

def as_record(report_id: str, res, rubric, full_text: str = "", name: str = "") -> dict:
    """把一次 GradingResult 转成班级学情要的记录形状。

    为什么要这个桥：界面批量评阅、CLI batch_run、预置演示结果三处都在产出
    「一份报告的判定」，但它们的数据形状一度各写各的。看板如果去猜形状，
    最容易出的错是**界面显示的总分与看板统计的总分来自不同字段** ——
    这种不一致比数字难看严重得多（与 metrics.py 单一口径是同一个道理）。
    统一从这里转，谁产出都长一个样。
    """
    items = {i.id: i for i in (rubric.items if hasattr(rubric, "items") else rubric)}
    details = []
    for j in res.judgements:
        it = items.get(j.rubric_item_id)
        details.append({
            "id": j.rubric_item_id,
            "name": (it.name if it is not None else j.rubric_item_id),
            "verdict": j.verdict,
            "s": j.score,
            "max": (it.max_score if it is not None else None),
            "confidence": j.confidence,
            "needs_review": bool(j.needs_review),
            "system_error": j.system_error or "",
            "evidence": [e.quote for e in j.evidence],
            "dropped": list(getattr(j, "dropped_quotes", []) or []),
        })
    info = {}
    if getattr(res, "run_info", None) is not None:
        try:
            import json as _json
            info = _json.loads(res.run_info.model_dump_json())
        except Exception:
            info = {}
    return {
        "report_id": report_id,
        "name": name or report_id,
        "total": res.total,
        "chars": len(full_text or ""),
        "coverage": info.get("parse_coverage", ""),
        "total_incomplete": bool(getattr(res, "total_incomplete", False)),
        "details": details,
    }


def as_batch(records: List[dict], course: str = "") -> dict:
    """记录列表 → 看板入参（同时带上课程名，便于导出留档）"""
    return {"reports": list(records or []), "course": course}
