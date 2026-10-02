# -*- coding: utf-8 -*-
"""班级学情看板（界面层）

这一页回答的不是"这份报告几分"，而是教师真正要决策的三个问题：
**这个班普遍卡在哪 / 我下一节课该讲什么 / 哪些学生需要我找一趟。**

三条显示纪律（与 classview 的计算纪律配套）：

1. **先说数据质量，再给结论**。系统错误未判定的、超长走召回的、样本不足的，
   都在结论**之前**讲清楚 —— 否则教师会拿一个掺了系统故障的统计去调整教学。
2. **每条结论都能点开看到依据**。建议不是模型生成的漂亮话，是确定性算术的结果，
   所以必须能把"哪几项、多少份、什么形态"摊开给教师核对。
3. **区分「判定引擎」与「聚合引擎」**。分数来自模型，共性短板来自代码；
   前者有波动、后者是算术。这个区分不写清楚，教师会把两者一起怀疑。
"""
import json
import os

import pandas as pd
import streamlit as st

import classview as CV

ROOT = os.path.dirname(os.path.abspath(__file__))
COHORT = os.path.join(ROOT, "data", "class_demo", "cohort.json")


def _load_cohort(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def available_sources() -> list:
    """看板的数据来源：演示队列 + 其它落盘的队列文件"""
    out = []
def _read_batch_run(path: str):
    """把 tools/batch_run.py 的产物转成看板入参；读不动就返回 None。

    CLI 与界面走的是同一套判定，产出形状也一样（都是 `details` 明细），
    所以这里不需要转换，只要确认它确实是批量结果而不是别的 JSON。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    reports = data.get("results") or data.get("reports") or []
    if not isinstance(reports, list) or not reports:
        return None
    if not any(r.get("details") for r in reports if isinstance(r, dict)):
        return None
    # course 有两个可能的位置：CLI 产物放在 config 里，队列文件放在顶层。
    # 只认其中一处会让另一个来源的课程名悄悄变成空串（界面上少一行说明，
    # 导出的留档里也少了上下文）—— 两处都看，取先有的那个。
    course = (data.get("config") or {}).get("course") or data.get("course") or ""
    return {"reports": reports, "course": course}


def available_sources() -> list:
    """看板的数据来源，按"最可能是教师刚跑出来的"排序：

    1. `data/results/batch_v2_*.json` —— CLI 跑 `tools/batch_run.py` 的产物。
       放在最前是有意的：教师刚在命令行跑完一个班，回到界面就该能立刻看到结论，
       而不是还要手动把文件拷到某个目录去。
    2. 演示队列与 `data/class_demo/` 下的其它队列。
    """
    out = []

    results_dir = os.path.join(ROOT, "data", "results")
    if os.path.isdir(results_dir):
        found = []
        for fn in os.listdir(results_dir):
            if not (fn.startswith("batch") and fn.endswith(".json")):
                continue
            p = os.path.join(results_dir, fn)
            if _read_batch_run(p) is not None:
                found.append((os.path.getmtime(p), fn, p))
        # 新的在前：教师刚跑的那次应当是第一眼看到的
        for _, fn, p in sorted(found, reverse=True):
            out.append((f"CLI 批量结果 · {fn}", p))

    if os.path.exists(COHORT):
        out.append(("演示班级（12 份，含自造样本）", COHORT))

    d = os.path.join(ROOT, "data", "class_demo")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if fn.endswith(".json") and os.path.abspath(p) != os.path.abspath(COHORT):
                # 只列真正的队列文件：`data/class_demo/` 下还放着一份
                # ocr_comparison.json（读图前后对照），它不是队列，
                # 列进下拉框只会让人选到一个打不开的东西。
                if _read_batch_run(p) is not None:
                    out.append((fn, p))
    return out


def load_source(path: str) -> dict:
    """读一个数据来源。CLI 产物与队列文件两种形状都支持。"""
    got = _read_batch_run(path)
    if got is not None:
        return got
    return _load_cohort(path)


def render(course_hint: str = "") -> None:
    st.subheader("班级学情")
    st.caption("把一批报告的评阅结果聚合成**教学决策**：共性短板、需要关注的学生、"
               "以及可以直接改进的地方。分数来自模型，共性短板是确定性算术。")

    sources = available_sources()
    batch_details = st.session_state.get("batch_details") or []

    picked = st.radio(
        "数据来源",
        ["本次界面批量评阅的结果", "已落盘的批量结果 / 演示队列"],
        horizontal=True,
        index=0 if batch_details else (1 if sources else 1),
        help="刚在界面跑完批量评阅就用第一项；用命令行跑过 tools/batch_run.py、"
             "或想先看这一页长什么样，就用第二项")

    if picked.startswith("本次界面"):
        if not batch_details:
            st.info("还没有本次批量结果。可以：\n\n"
                    "① 到上面的「多份报告批量评阅」跑一次；或\n"
                    "② 用命令行跑 `python tools/batch_run.py`，"
                    "它的产物会自动出现在另一个选项里")
            return
        cohort = {"reports": batch_details, "course": course_hint}
        quality_head = "本次结果来自你刚跑的界面批量评阅。"
    else:
        if not sources:
            st.info("没有可用的批量结果。生成方式：\n\n"
                    "```\n"
                    "python tools/batch_run.py        # 命令行批量评阅（结果落到 data/results/）\n"
                    "python tools/make_class_roster.py && python tools/build_class_demo.py"
                    "   # 或生成演示队列\n```")
            return
        labels = [s[0] for s in sources]
        label = st.selectbox("数据来源文件", labels)
        path = dict(sources)[label]
        cohort = load_source(path)
        if not cohort.get("reports"):
            st.warning(f"这个文件里没有可用的批量结果：`{os.path.relpath(path, ROOT)}`")
            return
        quality_head = f"数据来自 `{os.path.relpath(path, ROOT)}`（{label}）"

    agg = CV.aggregate(cohort)
    if not agg["n_scored"] and not agg["n_reports"]:
        st.warning("这份队列里没有可用的评阅结果。")
        return
    rows = CV.student_rows(cohort)
    insights = CV.build_insights(agg, rows, course=cohort.get("course") or course_hint)

    st.markdown("---")
    st.caption(quality_head)
    _render_quality(agg, cohort, insights)
    _render_overview(agg)
    _render_weak_items(agg)
    _render_actions(insights)
    _render_students(rows)
    _render_exemplars(insights)
    _render_export(cohort, agg, insights, rows)


# ---------------- 数据质量：必须在结论之前 ----------------

def _render_quality(agg, cohort, insights):
    issues = []
    if agg.get("n_incomplete"):
        issues.append(f"**{agg['n_incomplete']} 份含系统错误**（模型调用失败），"
                      f"总分不完整，已从均值与分布中剔除")
    if agg.get("n_retrieved_mode"):
        issues.append(f"**{agg['n_retrieved_mode']} 份正文超长走了关键词召回**，"
                      f"可信度低于全文模式")
    if agg.get("needs_review_total"):
        issues.append(f"**{agg['needs_review_total']} 处判定待人工复核**"
                      f"（低置信 / 两次不一致 / 分差过大），不能直接当成绩发布")
    n = agg.get("n_scored") or 0
    if n and n < CV.MIN_N_FOR_INSIGHT:
        issues.append(f"**有效样本只有 {n} 份**，比例只作参考，不要据此下"
                      f"「这个班普遍如何」的结论")

    if issues:
        st.markdown("**先看数据质量**（这几条会限制结论的强度）")
        for x in issues:
            st.warning(x)
    for nt in insights.get("notes", []):
        st.caption("· " + nt)


# ---------------- 总览 ----------------

def _render_overview(agg):
    t = agg["totals"]
    c = st.columns(5)
    c[0].metric("参评 / 有效", f"{agg['n_reports']} / {t['n']}")
    c[1].metric("平均分", t["mean"])
    c[2].metric("分数区间", f"{t['min']} ~ {t['max']}")
    c[3].metric("及格率（≥60）", f"{t['pass_rate']}%")
    c[4].metric("优秀率（≥85）", f"{t['excellent_rate']}%")

    dist = pd.DataFrame([{"分数段": b["label"], "人数": b["count"]}
                         for b in agg["distribution"]])
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**分数分布**")
        if dist["人数"].sum():
            st.bar_chart(dist.set_index("分数段"), height=240)
        else:
            st.caption("没有可用的总分（可能全部未判定）。")
    with right:
        st.markdown("**区间解读**")
        rng = t["range"]
        if t["n"] == 0:
            st.caption("无数据。")
        elif rng < 15:
            st.caption(f"极差只有 {rng} 分：**判定对好坏报告的区分度不足**，"
                       f"建议检查评分点的判定标准是否太笼统。")
        else:
            st.caption(f"极差 {rng} 分，说明这套标准能把不同水平的报告分开。")


# ---------------- 共性短板 ----------------

def _render_weak_items(agg):
    st.markdown("---")
    st.markdown("### 共性短板：这个班普遍卡在哪")
    items = [i for i in agg["per_item"] if i["counted"]]
    if not items:
        st.caption("没有可统计的评分点。")
        return
    items.sort(key=lambda i: -i["weak_ratio"])
    df = pd.DataFrame([{
        "评分点": i["name"],
        "完全缺失": i["miss"],
        "部分拿到": i["partial"],
        "拿满": i["hit"],
        "薄弱率": i["weak_ratio"],
        "该项均分": (f"{i['avg_score']}/{i['max_score']}"
                     if i["avg_score"] is not None else "—"),
        "待复核": i["needs_review"],
    } for i in items])
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={"薄弱率": st.column_config.ProgressColumn(
            "薄弱率（未拿满的比例）", min_value=0.0, max_value=1.0, format="%.0f%%")})
    st.caption("薄弱率 = （完全缺失 + 部分拿到）/ 参与统计的份数。"
               "**系统错误未判定的项不计入分母** —— 那属于系统的问题，不是学生没做到。")


# ---------------- 教学建议 ----------------

_TYPE_LABEL = {
    "teaching": "课堂教学",
    "brief": "作业说明",
    "rubric": "评分标准",
    "review": "需人工处理",
}


def _render_actions(insights):
    st.markdown("---")
    st.markdown("### 建议动作：下一轮可以怎么改")
    st.caption("每条建议都由左边的数据算出来，不是模型生成的漂亮话 —— "
               "点开「依据」可以看到是哪几项、多少份推出来的。")
    actions = insights.get("actions") or []
    if not actions:
        st.info("这份队列没有触发任何建议规则（例如各项都拿到了、也没有待复核项）。")
        return
    for a in actions:
        tag = _TYPE_LABEL.get(a["type"], a["type"])
        with st.expander(f"【{tag}】{a['title']}", expanded=(a["type"] in ("teaching", "brief"))):
            st.markdown(f"**为什么**：{a['detail']}")
            st.markdown(f"**可以怎么做**：{a['suggest']}")
            st.caption("依据（可核对）：" + json.dumps(a["evidence"], ensure_ascii=False))


# ---------------- 需要关注的学生 ----------------

def _render_students(rows):
    st.markdown("---")
    st.markdown("### 需要关注的学生")
    if not rows:
        st.caption("没有学生名单。")
        return
    focus = [r for r in rows if (r["total"] is not None and r["total"] < 60)
             or r["incomplete"] or r["needs_review"]]
    df = pd.DataFrame([{
        "报告": r["name"],
        "总分": r["total"],
        "最弱项": r["weakest"],
        "与该满分差": r["weakest_gap"],
        "完全缺失项数": r["miss_count"],
        "待复核": r["needs_review"],
        "总分不完整": "是" if r["incomplete"] else "",
    } for r in (focus or rows[:10])])
    if not focus:
        st.caption("没有明显落后的学生（无不及格、无系统错误、无待复核项）。"
                   "下面显示分数最低的几份供参考。")
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("排序按总分升序 —— 教师先看最需要找的那几个。")


def _render_exemplars(insights):
    """可当范例讲的学生。

    上面那条教学建议写着"用一份达标样本与一份不达标样本做对照"——
    没有现成样本的话，教师还得自己去找。这里直接给出来。
    """
    ex = insights.get("exemplars") or []
    if not ex:
        return
    st.markdown("---")
    st.markdown("### 可以直接当范例讲的报告")
    st.caption("条件是：无完全缺失项、无待复核项、总分完整。"
               "上面那条「课堂教学」建议如果需要对照样本，从这里取。")
    st.dataframe(pd.DataFrame(ex).rename(columns={"name": "报告", "total": "总分"}),
                 use_container_width=True, hide_index=True)


# ---------------- 导出 ----------------

def _render_export(cohort, agg, insights, rows):
    st.markdown("---")
    with st.expander("导出这份班级学情（留档 / 教研组共享）"):
        payload = {
            "course": cohort.get("course", ""),
            "aggregate": agg,
            "insights": insights,
            "students": rows,
        }
        st.download_button(
            "⬇️ 班级学情 JSON",
            json.dumps(payload, ensure_ascii=False, indent=1),
            "班级学情.json", "application/json", key="cls_json")
        st.download_button(
            "⬇️ 学生明细 CSV",
            pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig"),
            "班级学生明细.csv", "text/csv", key="cls_csv")
        st.caption("JSON 里含聚合结果、建议与依据，可直接交给教研组；"
                   "CSV 只有学生维度，便于进成绩表。")
