# -*- coding: utf-8 -*-
"""RAG-E 四阶流水线编排

R 评分点原子化 -> A 证据锚定判定 -> G 一致性守卫 -> E 反馈生成

三条铁律在这里落地：
1. total 由代码加总，模型输出里永远没有 total
2. verify_evidence 不通过 -> 重跑；再不通过 -> 降级为 miss + needs_review
3. 低置信 / 双跑不一致 -> needs_review = True
"""
import json
import time

from models import (Rubric, RubricItem, ItemJudgement, Evidence,
                    Feedback, GradingResult)
import prompts
from llm import call_json, verify_evidence, locate, get_env, demo_mode, get_stats
import parser as P


DEMO_RESULT = None   # 离线演示用（由 tools/make_demo.py 生成后载入）


# ---------- R：评分点原子化 ----------
def stage_rubric(raw_rubric: str, course_hint: str = "") -> Rubric:
    user = f"课程/实验背景：{course_hint or '计算机专业课程实验'}\n\n教师的评分标准原文：\n{raw_rubric}"
    rubric = call_json(prompts.S1_RUBRIC, user, Rubric)

    # 硬校验：总分归一到 100，点数不超过 12
    total = sum(i.max_score for i in rubric.items)
    if total != 100 and rubric.items:
        scale = 100.0 / total
        for it in rubric.items:
            it.max_score = round(it.max_score * scale, 1)
        diff = round(100 - sum(i.max_score for i in rubric.items), 1)
        if abs(diff) > 0.01:
            rubric.items[0].max_score = round(rubric.items[0].max_score + diff, 1)
    if len(rubric.items) > 12:
        rubric.items = rubric.items[:12]
    return rubric


# ---------- A：证据锚定判定 ----------
def stage_judge(item: RubricItem, sections, full_text: str, top_k: int = 4) -> ItemJudgement:
    picked = P.retrieve(sections, item.positive_signals, top_k=top_k)
    ctx = "\n\n".join(f"[{s.id}] {s.title}\n{s.text[:2500]}" for s in picked)

    user = (f"评分点：{item.name}\n"
            f"判定标准：{item.criteria}\n"
            f"满分：{item.max_score} 分\n"
            f"命中特征：{'、'.join(item.positive_signals) or '无'}\n"
            f"未命中特征：{'、'.join(item.negative_signals) or '无'}\n\n"
            f"以下是报告中的相关片段（方括号内是章节编号）：\n{ctx}")

    j = call_json(prompts.S2_JUDGE, user, ItemJudgement)
    j.rubric_item_id = item.id

    # 防线一：caps 与取值合法性
    if j.verdict not in ("hit", "partial", "miss"):
        j.verdict = "partial"
    j.score = max(0.0, min(float(j.score), float(item.max_score)))
    j.confidence = max(0.0, min(float(j.confidence), 1.0))

    # 防线二：引用必须原文逐字存在，否则重跑一次
    if not verify_evidence(j, full_text):
        retry_user = (user + "\n\n【上一次判定被驳回】你给出的 quote 无法在报告原文中逐字匹配。"
                      "必须改为从上面片段里原样复制的原句，不得改写、不得概括。"
                      "若确实找不到依据，请判 miss 并把 evidence 给空数组。")
        try:
            j2 = call_json(prompts.S2_JUDGE, retry_user, ItemJudgement)
            j2.rubric_item_id = item.id
            if j2.verdict not in ("hit", "partial", "miss"):
                j2.verdict = "partial"
            j2.score = max(0.0, min(float(j2.score), float(item.max_score)))
            if verify_evidence(j2, full_text):
                j = j2
            else:
                j.verdict, j.score, j.needs_review = "miss", 0.0, True
                j.reason = (j.reason or "") + " （两次引用均未通过原文校验，已降级为待人工复核）"
        except Exception:
            j.verdict, j.score, j.needs_review = "miss", 0.0, True

    # 补齐位置信息
    for ev in j.evidence:
        sec, pos = locate(sections, full_text, ev.quote)
        ev.section_id = sec or ev.section_id
        ev.char_start = pos
    return j


# ---------- G：一致性守卫 ----------
def stage_consistency(item: RubricItem, j: ItemJudgement, sections, full_text: str,
                      threshold: float = 0.7) -> ItemJudgement:
    if j.confidence >= threshold and j.verdict == "hit":
        return j
    picked = P.retrieve(sections, item.positive_signals, top_k=4)
    ctx = "\n\n".join(f"[{s.id}] {s.title}\n{s.text[:2500]}" for s in picked)
    user = (f"评分点：{item.name}\n判定标准：{item.criteria}\n\n"
            f"报告相关片段：\n{ctx}")
    try:
        j2 = call_json(prompts.S3_RECHECK, user, ItemJudgement, temperature=0.3)
        if j2.verdict != j.verdict:
            j.needs_review = True
            j.reason = (j.reason or "") + f" （二次判定为 {j2.verdict}，两次不一致，已转人工复核）"
    except Exception:
        pass
    return j


# ---------- E：反馈生成 ----------
def stage_feedback(items, judgements) -> Feedback:
    lines = []
    for it in items:
        jd = next((x for x in judgements if x.rubric_item_id == it.id), None)
        if not jd:
            continue
        quotes = " / ".join(e.quote for e in jd.evidence) or "无"
        lines.append(f"- {it.id} {it.name}：{jd.verdict}，{jd.score}/{it.max_score} 分。"
                     f"理由：{jd.reason}。证据原文：{quotes}")
    user = "逐项判定结果：\n" + "\n".join(lines)
    try:
        return call_json(prompts.S4_FEEDBACK, user, Feedback)
    except Exception:
        return Feedback(summary="（反馈生成失败，请查看逐项判定）", suggestions=[])


# ---------- 主流程 ----------
def run_grading(full_text: str, sections, raw_rubric: str,
                report_id: str = "", course_hint: str = "",
                rubric: Rubric = None, enable_recheck: bool = True,
                progress=None) -> GradingResult:
    t0 = time.time()

    items = rubric.items if rubric else stage_rubric(raw_rubric, course_hint).items

    judgements = []
    n = len(items)
    for i, item in enumerate(items, 1):
        if progress:
            progress(i, n, item.name)
        try:
            j = stage_judge(item, sections, full_text)
        except Exception as e:
            j = ItemJudgement(rubric_item_id=item.id, verdict="miss", score=0.0,
                              confidence=0.0, reason=f"调用失败：{str(e)[:120]}",
                              needs_review=True)
        if enable_recheck:
            j = stage_consistency(item, j, sections, full_text)
        judgements.append(j)

    # 铁律一：总分由代码加总
    total = round(sum(j.score for j in judgements), 1)
    feedback = stage_feedback(items, judgements)

    return GradingResult(
        report_id=report_id, items=items, judgements=judgements,
        total=total, feedback=feedback,
        model=get_env("LLM_MODEL", ""), elapsed_sec=round(time.time() - t0, 1),
    )


def result_to_json(res: GradingResult) -> dict:
    return json.loads(res.model_dump_json())
