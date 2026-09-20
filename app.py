# -*- coding: utf-8 -*-
"""AutoGrader · 实验报告智能评阅平台（Streamlit 界面）

启动：  streamlit run app.py

四个标签页：
  tab1 评阅   —— 上传报告 / 选样本 / 输入评分标准 / 跑流水线
  tab2 详情   —— 原文高亮对照 + 逐评分点卡片 + 人工覆盖改分
  tab3 评分点 —— 原子化结果预览与分值编辑
  tab4 导出   —— 成绩表 CSV / 结果 JSON
"""
import os
import json
import time
import datetime

import streamlit as st

import parser as P
import prompts
from models import Rubric, RubricItem
from pipeline import run_grading, stage_rubric
from llm import get_env, demo_mode

ROOT = os.path.dirname(os.path.abspath(__file__))
# 本地优先用完整脱敏样本（data/samples，不入库）；云端仓库只有公开裁剪版 samples_demo
if os.path.isdir(os.path.join(ROOT, "data", "samples")):
    SAMPLES = os.path.join(ROOT, "data", "samples")
    _SAMPLE_NOTE = ""
else:
    SAMPLES = os.path.join(ROOT, "samples_demo")
    _SAMPLE_NOTE = "云端演示样本为「证据窗口裁剪版」（与公开案例一致），本地运行请使用完整样本"
RESULTS = os.path.join(ROOT, "data", "results")
IS_CLOUD = not os.path.isdir(os.path.join(ROOT, "data", "samples"))

st.set_page_config(page_title="AutoGrader", page_icon="📋", layout="wide")

st.markdown("""
<style>
  .big {font-size:30px; font-weight:600; line-height:1.2}
  .sub {color:#8b8a85; font-size:13px}
  .txtbox {white-space:pre-wrap; line-height:1.95; font-size:14px;
           background:#fafaf8; border:1px solid #e3e2dd; border-radius:10px; padding:14px}
  mark {background:#ffe08a; padding:1px 0}
  mark.on {background:#ffb020}
</style>
""", unsafe_allow_html=True)


# ---------- 工具 ----------
@st.cache_data(show_spinner=False)
def load_text(sample: str):
    path = os.path.join(SAMPLES, sample)
    with open(path, encoding="utf-8") as f:
        full = f.read()
    return full, P.split_sections(full)


def highlight(text, quotes, active=None):
    import html
    out = html.escape(text)
    for q in sorted(set(q for q in quotes if q and len(q) >= 6), key=len, reverse=True):
        cls = ' class="on"' if q == active else ''
        out = out.replace(html.escape(q), f"<mark{cls}>{html.escape(q)}</mark>")
    return out


def badge(v):
    m = {"hit": ("#e1f5ee", "#0f6e56", "命中"),
         "partial": ("#faeeda", "#854f0b", "部分命中"),
         "miss": ("#f1efe8", "#5f5e5a", "未命中")}
    bg, fg, label = m.get(v, m["miss"])
    return (f'<span style="background:{bg};color:{fg};font-size:12px;'
            f'padding:2px 9px;border-radius:999px">{label}</span>')


def save_result(res, name):
    os.makedirs(RESULTS, exist_ok=True)
    p = os.path.join(RESULTS, f"{name}.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(res.model_dump_json(ensure_ascii=False, indent=2))
    return p


# ---------- 侧边栏 ----------
with st.sidebar:
    st.markdown("### AutoGrader")
    st.caption("RAG-E 四阶判定链")
    st.markdown(f"模型：`{get_env('LLM_MODEL','未配置')}`")
    if demo_mode():
        st.warning("DEMO_MODE 已开启：不会调用真实模型")
    st.markdown("---")
    st.markdown("**三条铁律**")
    st.caption("① 分数由代码加总，模型只输出单点判定")
    st.caption("② 无证据的判断不算数，引用必须原文匹配")
    st.caption("③ 不确定就交给人，标记待复核")

st.title("AutoGrader · 实验报告智能评阅平台")
st.caption("把老师的评分标准变成可核查、可溯源、可校准的判定流水线")

tab1, tab2, tab3, tab4 = st.tabs(["① 评阅", "② 详情对照", "③ 评分点", "④ 导出"])

# ---------- tab1 评阅 ----------
with tab1:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("选择报告")
        mode = st.radio("来源", ["内置脱敏样本", "上传文件"], horizontal=True)
        if mode == "内置脱敏样本":
            files = sorted(f for f in os.listdir(SAMPLES) if f.startswith("S")) \
                if os.path.isdir(SAMPLES) else []
            if not files:
                st.warning("未找到内置样本，请改用「上传文件」")
                full_text, sections, report_id = "", [], ""
            else:
                if _SAMPLE_NOTE:
                    st.caption(_SAMPLE_NOTE)
                pick = st.selectbox("样本", files)
                full_text, sections = load_text(pick)
                report_id = os.path.splitext(pick)[0]
        else:
            up = st.file_uploader("上传 PDF / DOCX / TXT", type=["pdf", "docx", "txt", "md"])
            if up:
                os.makedirs(RESULTS, exist_ok=True)
                tmp = os.path.join(RESULTS, "_upload_" + up.name)
                with open(tmp, "wb") as f:
                    f.write(up.getbuffer())
                full_text, sections = P.parse_file(tmp)
                report_id = os.path.splitext(up.name)[0]
            else:
                full_text, sections, report_id = "", [], ""
        if full_text:
            st.success(f"已载入 {report_id}：{len(full_text)} 字 / {len(sections)} 章节")

    with c2:
        st.subheader("评分标准")
        default_rubric = st.session_state.get("raw_rubric",
            "实验目的明确；实验步骤完整可复现；有运行结果或数据；有结果分析；"
            "代码规范有注释；有实验总结与心得。总分100")
        raw_rubric = st.text_area("用自然语言写评分标准", value=default_rubric, height=120)
        course = st.text_input("课程 / 实验背景", value="计算机专业课程实验")

    st.markdown("---")
    col_a, col_b, col_c = st.columns([1, 1, 2])
    with col_a:
        do_atom = st.button("① 生成评分点", use_container_width=True)
    with col_b:
        run = st.button("② 开始评阅", type="primary", use_container_width=True)
    with col_c:
        st.caption("先生成评分点、确认无误后再评阅 — 这是「人在回路」的第一道关口")

    if do_atom and full_text:
        st.session_state["raw_rubric"] = raw_rubric
        with st.spinner("R 阶段：把自然语言评分标准原子化…"):
            try:
                rub = stage_rubric(raw_rubric, course)
                st.session_state["rubric"] = rub
                st.success(f"已生成 {len(rub.items)} 个评分点（请到「评分点」页确认）")
            except Exception as e:
                st.error(f"生成失败：{e}")

    if run and full_text:
        st.session_state["raw_rubric"] = raw_rubric
        prog = st.progress(0)
        note = st.empty()

        def cb(i, n, name):
            prog.progress(i / n)
            note.caption(f"A 阶段：正在判定第 {i}/{n} 个评分点 —— {name}")

        rub = st.session_state.get("rubric")
        with st.spinner("RAG-E 流水线运行中…"):
            res = run_grading(full_text, sections, raw_rubric,
                              report_id=report_id, course_hint=course,
                              rubric=rub, progress=cb)
        st.session_state["result"] = res
        st.session_state["full_text"] = full_text
        st.session_state["sections"] = sections
        save_result(res, report_id)
        prog.empty(); note.empty()
        st.success(f"评阅完成：总分 {res.total} 分，耗时 {res.elapsed_sec} 秒")

# ---------- tab2 详情 ----------
with tab2:
    res = st.session_state.get("result")
    if not res:
        st.info("请先在「评阅」页跑一次评阅。")
    else:
        ft = st.session_state["full_text"]
        top = st.container()
        with top:
            c1, c2, c3 = st.columns([1, 1, 2])
            c1.markdown(f"<div class='big'>{res.total}</div><div class='sub'>总分 / 100（由代码加总）</div>",
                        unsafe_allow_html=True)
            c2.markdown(f"<div class='big'>{len(res.judgements)}</div><div class='sub'>评分点</div>",
                        unsafe_allow_html=True)
            nr = sum(1 for j in res.judgements if j.needs_review)
            c3.markdown(f"<div class='big'>{nr}</div><div class='sub'>待人工复核（低置信或两次判定不一致）</div>",
                        unsafe_allow_html=True)

        all_quotes = [e.quote for j in res.judgements for e in j.evidence]
        L, R = st.columns([1, 1])
        with L:
            st.markdown("#### 报告原文（命中证据高亮）")
            st.markdown(f"<div class='txtbox'>{highlight(ft, all_quotes)}</div>",
                        unsafe_allow_html=True)
        with R:
            st.markdown("#### 逐评分点判定")
            active = st.session_state.get("active_q")
            if active:
                st.caption(f"已选中证据：{active[:40]}…（左侧深黄高亮）")
            for item in res.items:
                j = next((x for x in res.judgements if x.rubric_item_id == item.id), None)
                if not j:
                    continue
                with st.expander(f"{item.name}　{j.score}/{item.max_score}", expanded=False):
                    st.markdown(badge(j.verdict) +
                                (f"　<font color='#a32d2d'>待复核</font>" if j.needs_review else "") +
                                f"　置信度 `{j.confidence:.2f}`", unsafe_allow_html=True)
                    st.caption(j.reason)
                    for e in j.evidence:
                        st.markdown(f"> 「{e.quote}」　`{e.section_id}`")
                        if st.button("定位", key=f"loc_{item.id}_{e.char_start}"):
                            st.session_state["active_q"] = e.quote
                            st.rerun()
                    st.markdown("**人工覆盖**")
                    ns = st.number_input("改分", 0.0, float(item.max_score), float(j.score),
                                         step=0.5, key=f"ov_{item.id}")
                    why = st.text_input("改分理由（留痕用）", key=f"why_{item.id}")
                    if abs(ns - j.score) > 0.01:
                        st.warning(f"覆盖记录：{item.name} {j.score} → {ns}"
                                   + (f"，理由：{why}" if why else "（未填理由）"))

        if res.feedback:
            st.markdown("---")
            st.markdown("#### 评语与改进建议")
            st.write(res.feedback.summary)
            for i, s in enumerate(res.feedback.suggestions, 1):
                st.markdown(f"{i}. {s}")

# ---------- tab3 评分点 ----------
with tab3:
    rub = st.session_state.get("rubric") or (
        st.session_state.get("result").items if st.session_state.get("result") else None)
    if not rub:
        st.info("请先在「评阅」页生成评分点。")
    else:
        st.caption("这是教师确认与校准的关口：可以改分、可以增删，改完重新评阅即刻生效。")
        items = rub.items if hasattr(rub, "items") else rub
        total = 0.0
        for it in items:
            c1, c2, c3 = st.columns([2, 1, 3])
            c1.markdown(f"**{it.id} {it.name}**")
            v = c2.number_input("分值", 0.0, 100.0, float(it.max_score), step=1.0,
                                key=f"score_{it.id}", label_visibility="collapsed")
            it.max_score = v
            total += v
            c3.caption(it.criteria)
        st.markdown("---")
        st.markdown(f"**合计分值：{round(total,1)}**" + ("　✅" if abs(total - 100) < 0.01 else "　⚠ 不等于 100"))

# ---------- tab4 导出 ----------
with tab4:
    res = st.session_state.get("result")
    if not res:
        st.info("暂无结果可导出。")
    else:
        import pandas as pd
        rows = []
        for it in res.items:
            j = next((x for x in res.judgements if x.rubric_item_id == it.id), None)
            if not j:
                continue
            rows.append({
                "评分点": it.name, "判定": j.verdict, "得分": j.score, "满分": it.max_score,
                "置信度": j.confidence, "待复核": "是" if j.needs_review else "",
                "理由": j.reason,
                "证据原文": " | ".join(e.quote for e in j.evidence),
            })
        rows.append({"评分点": "总分", "判定": "", "得分": res.total, "满分": 100,
                     "置信度": "", "待复核": "", "理由": "由代码加总", "证据原文": ""})
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
        st.download_button("下载 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           f"{res.report_id}_评阅结果.csv", "text/csv")
        st.download_button("下载 JSON", res.model_dump_json(ensure_ascii=False, indent=2),
                           f"{res.report_id}.json", "application/json")
        st.caption(f"结果同时已保存至 data/results/{res.report_id}.json")
