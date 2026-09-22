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
    # 自适应上下文：≤40000 字给全文，超长才走召回。
    # 这里必须用全文——实测 retrieve(top_k=4)+每章 2500 截断会让模型只看到报告 7%~11% 的内容，
    # 导致大量假阴性 miss（代码/表格/截图类内容基本全丢）。详见 docs/bugfix-上下文丢失.md
    ctx, _is_full = P.build_context(sections, full_text, item.positive_signals, top_k=top_k)

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
    ctx, _is_full = P.build_context(sections, full_text, item.positive_signals, top_k=6)
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
def fallback_feedback(items, judgements, err: Exception) -> Feedback:
    """E 阶段失败时的兜底评语：**由代码**根据已锁定的判定结果拼装，不由模型再生成一次。

    纪律边界（不要越线）：
    - 这里不做任何重新判定，也不放宽任何规则；只是把 A/G 阶段已经通过原文校验的
      结论，改写成学生看得懂的话。
    - 分数、verdict、evidence 一律不变，仍然由 A/G 阶段和代码加总决定。
    - 因此本函数对 MAE / 误差≤5 占比 / 证据可溯源率 三个指标**没有任何影响**。
    """
    vmap = {"hit": "完全达标", "partial": "部分达标", "miss": "未达标"}
    per_item, weak, ok_names = {}, [], []
    total = round(sum(float(j.score) for j in judgements), 1)

    for it in items:
        jd = next((x for x in judgements if x.rubric_item_id == it.id), None)
        if not jd:
            continue
        lost = round(float(it.max_score) - float(jd.score), 1)
        if jd.verdict == "miss":
            per_item[it.id] = (f"未达标，{jd.score}/{it.max_score} 分。该点要求是「{it.criteria}」，"
                               f"报告中未找到可支撑的原文依据。")
            weak.append((it, lost))
        elif jd.verdict == "partial":
            per_item[it.id] = (f"部分达标，{jd.score}/{it.max_score} 分，还可争取 {lost} 分。"
                               f"待补强之处：{it.criteria}。")
            weak.append((it, lost))
        else:
            per_item[it.id] = f"完全达标，{jd.score}/{it.max_score} 分。"
            ok_names.append(it.name)

    weak_txt = "、".join(f"{it.name}（-{lost}）" for it, lost in weak) or "无"
    ok_txt = "、".join(ok_names) or "暂无完全达标的评分点"
    headline = (f"总分 {total}/100。完全达标：{ok_txt}；主要失分点：{weak_txt}。")
    notice = (f"\n\n（说明：本次评语由规则引擎兜底生成，AI 反馈生成环节失败："
              f"{type(err).__name__}。逐项判定与分数不受影响，仍附原文证据。）")

    suggestions = []
    for it, lost in sorted(weak, key=lambda x: -x[1])[:3]:
        sig = "、".join([s for s in it.positive_signals if s and s != "无"][:3])
        tail = f"，建议在报告里明确写出并展示 {sig} 等内容" if sig else ""
        suggestions.append(
            f"补齐「{it.name}」（满分 {it.max_score}，现失 {lost} 分）："
            f"判定标准是「{it.criteria}」{tail}。")
    if not suggestions:
        suggestions.append("全部评分点均已达标，可在结果分析的深度与创新思考上继续加分。")

    return Feedback(summary=headline + notice, per_item=per_item, suggestions=suggestions,
                    generated_by="fallback", error=f"{type(err).__name__}: {str(err)[:300]}")


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
    except Exception as e:
        # 老实现会把异常吞成一句「生成失败」，线上查不到原因。
        # 现在：打到日志 + 写进 Feedback.error 字段（界面可见）+ 给出规则兜底评语。
        print(f"[pipeline] E 阶段反馈生成失败：{type(e).__name__}: {str(e)[:300]}")
        return fallback_feedback(items, judgements, e)


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
