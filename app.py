# -*- coding: utf-8 -*-
"""AutoGrader · 实验报告智能评阅平台（Streamlit 界面）

启动：  streamlit run app.py

五个标签页：
  tab1 评阅   —— 上传报告 / 选样本 / 输入评分标准 / 跑流水线（含原始版面对照）
  tab2 详情   —— 原文高亮对照 + 逐评分点卡片 + 人工覆盖改分
  tab3 评分点 —— 原子化结果预览与分值编辑（改完必须合计正好 100）
  tab4 导出   —— 成绩表 CSV / 结果 JSON（含 AI 分、最终分与改分记录）
  tab5 批量测试 —— 多份批量评阅 / 同一份重复评阅（一致性）
"""
import os
import re
import json
import time
import uuid
import tempfile
import datetime

import streamlit as st

import parser as P
import prompts
import batch_ui
from models import Rubric, RubricItem
from pipeline import (run_grading, stage_rubric, rubric_source_hash,
                      validate_rubric, RubricError, apply_override,
                      effective_score, recompute_total, build_export_rows)
from llm import get_env, demo_mode

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _safe_display_name(name: str) -> str:
    """只保留用于展示的安全文件名：去路径、去控制字符、限制长度"""
    base = os.path.basename(name or "")
    base = re.sub(r"[\x00-\x1f/\\]", "", base)
    return base[:60] or "未命名报告"


_UPLOAD_CACHE = {}          # 只留最近一次上传的结果，避免每次交互都重新落盘 + 重渲染


def load_uploaded(up):
    """上传 -> 解析 -> 渲染版面预览 -> 删除临时文件。

    同一个文件在页面重跑时会被复用缓存结果：Streamlit 每次交互都会重跑整个脚本，
    旧实现会让"点一次应用改分"触发一次重新落盘 + 重新渲染 20 页 PDF。

    report_id 用随机 UUID（不参与任何路径拼接），原始文件名只保留净化后的展示名；
    临时文件无论成败都在 finally 中删除。
    版面预览必须在删除临时文件**之前**渲染，且只保存在内存里，不落盘。
    """
    key = f"{getattr(up, 'file_id', '')}:{up.name}:{getattr(up, 'size', '')}"
    if _UPLOAD_CACHE.get("key") == key:
        return _UPLOAD_CACHE["value"]

    tmp = _save_upload_to_temp(up)
    try:
        full_text, sections = P.parse_file(tmp)
        try:
            images, total_pages = P.render_pdf_pages(tmp), P.pdf_page_count(tmp)
        except Exception as e:              # 渲染失败绝不能影响评阅主流程
            print(f"[warn] 版面渲染失败（不影响评阅）：{type(e).__name__}: {e}")
            images, total_pages = [], 0
    finally:
        try:
            os.remove(tmp)
        except OSError as e:
            print(f"[warn] 临时上传文件未能删除：{tmp}（{e}）")
    value = (full_text, sections, "UP-" + uuid.uuid4().hex[:8],
             _safe_display_name(up.name), images, total_pages)
    _UPLOAD_CACHE.clear()
    _UPLOAD_CACHE.update(key=key, value=value)
    return value


@st.cache_data(show_spinner=False)
def _render_cached(path: str, mtime: float, max_pages: int):
    """样本的版面渲染结果按「路径 + 修改时间」缓存，避免每次交互都重渲染"""
    return P.render_pdf_pages(path, max_pages=max_pages)


def load_sample(pick):
    """读取内置脱敏样本；若本机保留了对应的原始 PDF，则顺带渲染版面预览"""
    full_text, sections = load_text(pick)
    rid = os.path.splitext(pick)[0]
    images, total_pages = [], 0
    orig = os.path.join(ROOT, "data", "raw", rid + ".pdf")
    if os.path.exists(orig):
        try:
            images = _render_cached(orig, os.path.getmtime(orig), P.PREVIEW_MAX_PAGES)
            total_pages = P.pdf_page_count(orig)
        except Exception as e:
            print(f"[warn] 样本版面渲染失败（不影响评阅）：{type(e).__name__}: {e}")
    return full_text, sections, rid, images, total_pages


def show_page_preview():
    """展示原始版面：只渲染 PDF，图片内容不参与判定"""
    images = st.session_state.get("page_images") or []
    if not images:
        return
    total = st.session_state.get("page_images_total", len(images))
    with st.expander(f"原始版面对照（{len(images)} 页，供人工核对）", expanded=False):
        st.caption(P.preview_caption(len(images), total))
        for i, img in enumerate(images, 1):
            st.image(img, caption=f"第 {i} 页", use_container_width=True)


def sweep_stale_uploads(days: int = 1):
    """启动时清扫超期的上传残留（正常路径都会在 finally 里删掉，这里是兜底）"""
    if not os.path.isdir(TMPDIR):
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for name in os.listdir(TMPDIR):
        p = os.path.join(TMPDIR, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                n += 1
        except OSError:
            pass                   # 删不掉就留着，下次再试
    if n:
        print(f"[cleanup] 清掉 {n} 个超期上传残留（>{days} 天）")
    return n


ALLOWED_EXTS = (".pdf", ".docx", ".txt", ".md")


def _sniff_ok(data: bytes, ext: str) -> bool:
    """粗略校验文件内容与扩展名是否一致（防止把二进制改名成 .txt 混进来）"""
    if ext in (".txt", ".md"):
        return b"\x00" not in data[:4096]          # 文本文件不该含 NUL 字节
    if ext == ".pdf":
        return data[:5] == b"%PDF-"
    if ext == ".docx":
        return data[:2] == b"PK"                    # docx 本质是 zip
    return False


def _save_upload_to_temp(up) -> str:
    """把上传内容写进受控临时目录，文件名用 UUID，避免路径穿越与同名覆盖。

    扩展名不在白名单、或内容与扩展名明显不符时**直接拒绝**——
    旧实现把未知扩展名静默当成 .txt，二进制文件会被评出满屏 miss，
    比拒绝上传糟糕得多。
    """
    os.makedirs(TMPDIR, exist_ok=True)
    ext = os.path.splitext(_safe_display_name(up.name))[1].lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"不支持的文件类型 {ext or '（无扩展名）'}，"
                         f"只接受 {'、'.join(ALLOWED_EXTS)}")
    data = up.getbuffer()
    if not _sniff_ok(bytes(data[:4096]), ext):
        raise ValueError(f"文件内容与扩展名 {ext} 不符（可能被改过名），已拒绝上传")
    path = os.path.join(TMPDIR, f"{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path

ROOT = os.path.dirname(os.path.abspath(__file__))
# 本地优先用完整脱敏样本（data/samples，不入库）；云端仓库只有公开裁剪版 samples_demo
if os.path.isdir(os.path.join(ROOT, "data", "samples")):
    SAMPLES = os.path.join(ROOT, "data", "samples")
    _SAMPLE_NOTE = ""
else:
    SAMPLES = os.path.join(ROOT, "samples_demo")
    _SAMPLE_NOTE = "云端演示样本为「证据窗口裁剪版」（与公开案例一致），本地运行请使用完整样本"
RESULTS = os.path.join(ROOT, "data", "results")
TMPDIR = os.path.join(ROOT, "data", "tmp_uploads")
IS_CLOUD = not os.path.isdir(os.path.join(ROOT, "data", "samples"))


def _csv_safe(v):
    """CSV 公式注入防护：以 = + - @ 开头的单元格前面加单引号"""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s

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
    # 必须走 parse_file（含空白归一），不能自己 open 读原文：
    # 判定用的上下文和引用校验用的正文必须是同一份规范文本，
    # 两边不一致会把好报告活活判成 0 分（见 docs/bugfix-引用匹配与PDF断行.md）
    return P.parse_file(os.path.join(SAMPLES, sample))


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
        st.warning("DEMO_MODE 已开启：评阅不会调用真实模型（需先跑 tools/make_demo.py 生成演示结果）")
    st.markdown("---")
    st.markdown("**三条铁律**")
    st.caption("① 分数由代码加总，模型只输出单点判定")
    st.caption("② 无证据的判断不算数，引用必须原文匹配")
    st.caption("③ 不确定就交给人，标记待复核")

st.title("AutoGrader · 实验报告智能评阅平台")
st.caption("把老师的评分标准变成可核查、可溯源、可校准的判定流水线")

sweep_stale_uploads()      # 兜底清扫超期上传残留（正常路径都会在 finally 里删掉）

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["① 评阅", "② 详情对照", "③ 评分点", "④ 导出", "⑤ 批量测试"])

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
                full_text, sections, report_id, imgs, total_pages = load_sample(pick)
                st.session_state["page_images"] = imgs
                st.session_state["page_images_total"] = total_pages
        else:
            st.warning("上传的报告正文会被发送到本项目配置的模型服务（见侧边栏显示的模型）。"
                       "**请勿上传含真实姓名/学号的未脱敏作业。**")
            up = st.file_uploader("上传 PDF / DOCX / TXT（≤ 20MB）",
                                  type=["pdf", "docx", "txt", "md"])
            if up:
                if up.size and up.size > MAX_UPLOAD_BYTES:
                    st.error(f"文件过大（{up.size/1048576:.1f}MB），上限 20MB。")
                    full_text, sections, report_id = "", [], ""
                else:
                    try:
                        full_text, sections, report_id, disp, imgs, total_pages = load_uploaded(up)
                    except ValueError as e:
                        st.error(f"上传被拒绝：{e}")
                        full_text, sections, report_id, imgs, total_pages = "", [], "", [], 0
                    except Exception as e:
                        st.error(f"解析失败（{type(e).__name__}）：{e}")
                        full_text, sections, report_id, imgs, total_pages = "", [], "", [], 0
                    st.session_state["display_name"] = disp if full_text else ""
                    st.session_state["page_images"] = imgs
                    st.session_state["page_images_total"] = total_pages
            else:
                full_text, sections, report_id = "", [], ""
        if full_text:
            st.success(f"已载入 {report_id}：{len(full_text)} 字 / {len(sections)} 章节")
            if st.session_state.get("page_images"):
                st.caption(f"已渲染原始版面 {len(st.session_state['page_images'])} 页，"
                           f"可在「详情对照」页人工核对（图片内容不参与自动判定）")
            # 解析体检警告（疑似扫描件 / 超长召回模式）在这里就说清楚，不等到出分
            for w in P.inspect_text(full_text, len(sections))["warnings"]:
                st.warning(w)
            show_page_preview()      # 评阅前就能人工核对版面

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

    # P1-2：rubric 是否过期 —— 教师改了评分标准原文或课程背景，旧 rubric 必须失效
    rub_now = st.session_state.get("rubric")
    if rub_now is not None:
        try:
            want_hash = rubric_source_hash(raw_rubric, course)
        except Exception:
            want_hash = ""
        got_hash = getattr(rub_now, "source_hash", "")
        if got_hash and want_hash and got_hash != want_hash:
            st.warning("评分标准/课程背景已经改过，当前评分点是基于**旧文本**生成的。"
                       "请重新点「① 生成评分点」，否则会沿用过期标准。")

    if do_atom and full_text:
        st.session_state["raw_rubric"] = raw_rubric
        with st.spinner("R 阶段：把自然语言评分标准原子化…"):
            try:
                rub = stage_rubric(raw_rubric, course)
                st.session_state["rubric"] = rub
                st.session_state["result"] = None      # 评分点变了，旧结果一并作废
                st.success(f"已生成 {len(rub.items)} 个评分点（请到「评分点」页确认）")
            except RubricError as e:
                st.error(f"评分标准不合法，已拒绝使用：{e}")
            except Exception as e:
                st.error(f"生成失败：{type(e).__name__}: {e}")

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
            ov_n = len(res.overrides)
            sub1 = "总分 / 100（由代码加总" + ("，含人工改分）" if ov_n else "）")
            c1.markdown(f"<div class='big'>{res.total}</div><div class='sub'>{sub1}</div>",
                        unsafe_allow_html=True)
            c2.markdown(f"<div class='big'>{len(res.judgements)}</div><div class='sub'>评分点</div>",
                        unsafe_allow_html=True)
            nr = sum(1 for j in res.judgements if j.needs_review)
            syserr = sum(1 for j in res.judgements if j.system_error)
            c3.markdown(f"<div class='big'>{nr}</div><div class='sub'>"
                        f"待人工复核（低置信 / 两次不一致 / 分数差距大"
                        f"{f' / 系统错误 {syserr} 项' if syserr else ''}）</div>",
                        unsafe_allow_html=True)
            if syserr:
                st.error(f"有 {syserr} 个评分点因**系统错误**未能判定（不是学生失分），"
                         f"必须由教师人工给分：见下方逐项卡片里的红色提示。")
            if getattr(res, "total_incomplete", False):
                st.warning("⚠ 本次总分**不完整**：有评分点因系统错误未判定，"
                           "该分数不应被当作最终成绩，也不应计入任何误差统计。")
            if res.run_info and res.run_info.injection_hits:
                st.info("检测到报告中有疑似操纵评分的表述："
                        + "、".join(res.run_info.injection_hits)
                        + "（仅对证据落在命中位置附近的评分点标记了待复核；"
                          "我们不会改动学生原文）")
            if ov_n:
                st.caption(f"已应用 {ov_n} 处人工改分；AI 原始总分 {res.ai_total}，"
                           f"最终总分 {res.total}。导出文件里两者都会保留。")

        show_page_preview()

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
                eff = effective_score(res, item.id)
                ov = next((o for o in res.overrides if o.rubric_item_id == item.id), None)
                title = f"{item.name}　{eff}/{item.max_score}" + ("　（已人工改分）" if ov else "")
                with st.expander(title, expanded=False):
                    st.markdown(badge(j.verdict) +
                                (f"　<font color='#a32d2d'>待复核</font>" if j.needs_review else "") +
                                f"　置信度 `{j.confidence:.2f}`", unsafe_allow_html=True)
                    if j.system_error:
                        st.error(f"**系统错误，非学生失分**：{j.system_error}")
                    st.caption(j.reason)
                    if j.verdict == "miss":
                        # 零风险的"缺什么"提示：直接用评分标准里已写好的要求，
                        # 不对 miss 放宽任何规则，也不额外让模型多说一句
                        need = [s for s in item.positive_signals if s and s != "无"]
                        if need:
                            st.caption("该评分点要求包含：" + "、".join(need[:5]))
                    for e in j.evidence:
                        st.markdown(f"> 「{e.quote}」　`{e.section_id}`")
                        if st.button("定位", key=f"loc_{item.id}_{e.char_start}"):
                            st.session_state["active_q"] = e.quote
                            st.rerun()

                    st.markdown("**人工改分（终裁）**")
                    ns = st.number_input("改分", 0.0, float(item.max_score), float(eff),
                                         step=0.5, key=f"ov_{item.id}")
                    why = st.text_input("改分理由（必填，留痕用）", key=f"why_{item.id}")
                    if st.button("应用改分", key=f"apply_{item.id}"):
                        if abs(ns - eff) < 0.01:
                            st.info("分数没有变化，未应用。")
                        elif not (why or "").strip():
                            st.error("改分必须填写理由，否则不留痕——未应用。")
                        else:
                            apply_override(res, item.id, float(ns), (why or "").strip())
                            save_result(res, res.report_id)
                            st.success(f"已应用：{item.name} {eff} → {ns}，"
                                       f"总分 {res.total}（AI 原始 {res.ai_total}）")
                            st.rerun()
                    if ov:
                        st.caption(f"覆盖记录：AI {ov.original_score} → 教师 {ov.new_score}"
                                   f"，理由：{ov.reason}（{ov.created_at}）")

        if res.feedback:
            st.markdown("---")
            st.markdown("#### 评语与改进建议")
            fb = res.feedback
            # E 阶段失败时不再静默：明确告诉用户这是规则兜底，并给出真实原因
            if getattr(fb, "generated_by", "model") != "model":
                st.warning("AI 反馈生成环节失败，以下评语由规则引擎根据已锁定的逐项判定拼装；"
                           "分数与证据不受影响。失败原因：" + (fb.error or "未知")[:200])
            st.write(fb.summary)
            for i, s in enumerate(fb.suggestions, 1):
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
        if st.button("校验并应用这套评分点"):
            # 没点「生成评分点」直接评阅时，session_state["rubric"] 为空、这里退回的是 list，
            # 旧实现在这种情况下会抛 AttributeError 把界面打崩 —— 统一包成 Rubric。
            rub_obj = rub if isinstance(rub, Rubric) else Rubric(items=list(rub))
            try:
                # normalize=False：教师手设的分值必须合计正好 100，不允许按比例缩放
                validate_rubric(rub_obj, normalize=False)
                st.session_state["rubric"] = rub_obj
                st.session_state["result"] = None    # 评分点变了，旧结果作废
                st.success("校验通过（满分合计 100、id 唯一、分值均为正数），已应用；"
                           "之前的评阅结果已作废，请重新评阅。")
            except RubricError as e:
                st.error(f"校验未通过，未应用：{e}")
            except Exception as e:
                st.error(f"校验未通过（{type(e).__name__}）：{e}")

# ---------- tab4 导出 ----------
with tab4:
    res = st.session_state.get("result")
    if not res:
        st.info("暂无结果可导出。")
    else:
        import pandas as pd
        # 口径统一由 pipeline.build_export_rows 决定（有测试覆盖），这里只做 CSV 安全处理
        rows = [{k: _csv_safe(v) for k, v in row.items()}
                for row in build_export_rows(res)]
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("下载 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           f"{res.report_id}_评阅结果.csv", "text/csv")
        st.download_button("下载 JSON", res.model_dump_json(ensure_ascii=False, indent=2),
                           f"{res.report_id}.json", "application/json")
        if res.run_info:
            with st.expander("本次评分的可审计信息（用于事后复现）"):
                st.json(json.loads(res.run_info.model_dump_json()))
        st.caption(f"结果同时已保存至 data/results/{res.report_id}.json"
                   f"（含 AI 原始分、人工改分记录与运行元信息）")

# ---------- tab5 批量测试 ----------
with tab5:
    batch_ui.render(SAMPLES, RESULTS, _SAMPLE_NOTE,
                    custom_rubric=st.session_state.get("rubric"))
