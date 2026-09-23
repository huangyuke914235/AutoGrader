# -*- coding: utf-8 -*-
"""批量测试（界面的第 ⑤ 个标签页）

提供两种批量能力：

  模式 A · 多份报告批量评阅
      一次跑完选中的所有报告，出横向对比表；
      本地若存在封存的人工 gold.json，自动对齐算出 MAE / 误差≤5 占比 / 证据可溯源率。

  模式 B · 同一份报告重复评阅
      同一份报告连跑 N 次，看分数和判定到底稳不稳。
      这是「G 一致性守卫」存在的实证：稳不稳用数据说话，不许嘴上说稳。

纪律边界（很重要）：
    本模块只调用已经存在的 parser / pipeline，**不新增任何判定逻辑，不改分数，
    不放宽任何证据校验**。它属于「观测与展示」层，对评测指标本身没有影响。
    另外：只有使用「内置固定 5 项标准」时，与人工 gold 的对比才有意义——
    换成自定义评分点后维度对不上，界面会明确拦住这种错误比较。
"""
import os
import json
import time

import pandas as pd
import streamlit as st

import parser as P
from models import Rubric, RubricItem
from pipeline import run_grading
from llm import demo_mode, get_stats

ROOT = os.path.dirname(os.path.abspath(__file__))
GOLD_PATH = os.path.join(ROOT, "data", "gold", "gold.json")

# 与 tools/batch_run.py 保持同一套固定标准（人工 gold 就是按这 5 项打的）
try:
    from tools.batch_run import ITEMS as FIXED_ITEMS
except Exception:
    FIXED_ITEMS = [
        RubricItem(id="r1", name="实验目的明确", criteria="开头明确写出本次实验的目的与要掌握的能力",
                   max_score=15, positive_signals=["实验目的", "掌握", "目的"]),
        RubricItem(id="r2", name="环境与步骤", criteria="写清实验环境配置与可复现的操作步骤",
                   max_score=20, positive_signals=["环境", "步骤", "安装", "配置"]),
        RubricItem(id="r3", name="核心实现", criteria="给出核心代码、模型结构或关键实现说明",
                   max_score=25, positive_signals=["代码", "实现", "算法", "结构"]),
        RubricItem(id="r4", name="结果与数据", criteria="给出运行结果、截图、表格或实验数据",
                   max_score=20, positive_signals=["结果", "输出", "截图", "数据"]),
        RubricItem(id="r5", name="分析与总结", criteria="对结果进行分析讨论，并有总结或心得",
                   max_score=20, positive_signals=["分析", "总结", "心得", "结论"]),
    ]


def fixed_rubric():
    """每次返回一份干净的固定 rubric（深拷贝，避免被界面改脏）"""
    return Rubric(items=[i.model_copy(deep=True) for i in FIXED_ITEMS])


def load_gold():
    """读封存的人工分数；不存在返回 None（云端仓库不含这些私有数据）"""
    if not os.path.exists(GOLD_PATH):
        return None
    try:
        g = json.load(open(GOLD_PATH, encoding="utf-8"))
        return {rid: v["total"] for rid, v in g.get("reports", {}).items() if "total" in v} or None
    except Exception as e:
        st.warning(f"读取 gold.json 失败：{e}")
        return None


def count_trace(res, full_text):
    """证据可溯源率：**与 benchmark 共用同一口径**（metrics.py）

    过去这里只数"幸存的引用"，而幸存引用是已经通过原文校验的，结构上恒为 ~100%，
    于是界面显示 ~100%、主页写 96.9% —— 同一件事两个数。
    现在分母包含被剔除与被作废的引用，与 benchmark 完全一致。
    """
    import metrics
    return metrics.traceability_from_judgements([(j, full_text) for j in res.judgements])


def _read_report(path):
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8") as f:
            return f.read()
    full, _secs = P.parse_file(path)
    return full


def _run_one(name, samples_dir, rubric, enable_recheck):
    """跑一份报告，返回一行结果 + 明细"""
    path = os.path.join(samples_dir, name)
    # 用 parse_file 一次拿到「规范全文 + 章节」，不要再对已归一的文本做二次切分
    full, secs = P.parse_file(path)
    res = run_grading(full, secs, "", report_id=os.path.splitext(name)[0],
                      rubric=rubric, enable_recheck=enable_recheck)
    return full, res


# ---------------- 模式 A：多份批量评阅 ----------------
def _render_multi(samples_dir, rubric, rubric_is_fixed, enable_recheck, files):
    picks = st.multiselect("选择报告（可多选）", files,
                           default=files[:min(3, len(files))])
    gold = load_gold()
    if not gold:
        st.caption("未检测到人工 gold.json（该文件属私有数据，云端不存在），本次只看横向区分度。")
    st.caption(f"预计模型调用：{len(picks)} 份 × {len(rubric.items)} 个评分点"
               f"{'× 2（含复核）' if enable_recheck else ''} 次，请注意额度与耗时。")

    if st.button("开始批量评阅", type="primary", use_container_width=True):
        rows, details = [], []
        prog = st.progress(0)
        note = st.empty()
        checked_all = ok_all = 0
        for i, name in enumerate(picks, 1):
            rid = os.path.splitext(name)[0]
            note.caption(f"正在评阅 {i}/{len(picks)}：{name}")
            prog.progress(i / len(picks))
            try:
                full, res = _run_one(name, samples_dir, rubric, enable_recheck)
                c, o = count_trace(res, full)
                checked_all += c
                ok_all += o
                row = {"报告": rid, "字数": len(full),
                       "总分": res.total,
                       "待复核": sum(1 for j in res.judgements if j.needs_review),
                       "耗时s": res.elapsed_sec}
                for it in rubric.items:
                    j = next((x for x in res.judgements if x.rubric_item_id == it.id), None)
                    row[f"{it.id} {it.name}"] = f"{j.score}/{it.max_score}" if j else "—"
                if gold and rid in gold:
                    row["人工分"] = gold[rid]
                    row["差值"] = round(res.total - gold[rid], 1)
                details.append({"report_id": rid, "total": res.total,
                                "judgements": [j.model_dump() for j in res.judgements]})
                rows.append(row)
            except Exception as e:
                rows.append({"报告": rid, "总分": None, "备注": f"失败：{str(e)[:120]}"})
        prog.empty()
        note.empty()
        st.session_state["batch_rows"] = rows
        st.session_state["batch_trace"] = (checked_all, ok_all)
        st.session_state["batch_details"] = details
        st.success(f"批量评阅完成：{len([r for r in rows if r.get('总分') is not None])}/{len(picks)} 份成功")

    rows = st.session_state.get("batch_rows")
    if not rows:
        return
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    done = [r for r in rows if r.get("总分") is not None]
    if len(done) >= 2:
        c1, c2, c3 = st.columns(3)
        tot = sorted(r["总分"] for r in done)
        c1.metric("参评份数", len(done))
        c2.metric("分数区间", f"{tot[0]} ~ {tot[-1]}")
        c3.metric("极差", round(tot[-1] - tot[0], 1),
                  "能区分好坏 ✅" if tot[-1] - tot[0] >= 15 else "判定偏一律 ⚠")

    checked, ok = st.session_state.get("batch_trace", (0, 0))
    if checked:
        st.caption(f"证据可溯源率：{round(100*ok/checked, 1)}%（{ok}/{checked} 条引用在本报告原文中精确匹配）")

    if gold and rubric_is_fixed:
        diffs = [abs(r["差值"]) for r in done if "差值" in r]
        if diffs:
            mae = round(sum(diffs) / len(diffs), 2)
            acc5 = round(100 * sum(1 for d in diffs if d <= 5) / len(diffs), 1)
            st.markdown("#### 与人工 gold 对比")
            m1, m2, m3 = st.columns(3)
            m1.metric("MAE 平均绝对误差", f"{mae} 分")
            m2.metric("误差 ≤5 分占比", f"{acc5}%")
            m3.metric("对齐人工的报告数", f"{len(diffs)}/{len(done)}")
            st.caption("人工 gold 由非主程成员独立打分后封存，只用于这一次横向对比，不参与任何调参。")
    elif gold and not rubric_is_fixed:
        st.warning("当前用的是自定义评分点，与人工 gold 的评分维度对不上，"
                   "此处不计算 MAE——维度不一致时算出来的误差没有意义。")

    st.download_button("下载批量结果 CSV",
                       df.to_csv(index=False).encode("utf-8-sig"),
                       "batch_results.csv", "text/csv")


# ---------------- 模式 B：重复评阅一致性测试 ----------------
def _render_repeat(samples_dir, rubric, enable_recheck, files):
    pick = st.selectbox("选择报告", files)
    n = st.slider("重复次数", 2, 5, 3)
    st.caption(f"同一份报告、同一套评分点，独立跑 {n} 次。"
               f"预计模型调用 {n}×{len(rubric.items)}{'× 2' if enable_recheck else ''} 次。"
               f"这是「同一种子同一份报告，判定会不会漂」的直接证据。")
    if st.button("开始重复测试", type="primary", use_container_width=True):
        runs = []
        prog = st.progress(0)
        note = st.empty()
        for i in range(1, n + 1):
            note.caption(f"第 {i}/{n} 次评阅…")
            prog.progress(i / n)
            try:
                _full, res = _run_one(pick, samples_dir, rubric, enable_recheck)
                row = {"第N次": i, "总分": res.total, "耗时s": res.elapsed_sec,
                       "待复核": sum(1 for j in res.judgements if j.needs_review)}
                for it in rubric.items:
                    j = next((x for x in res.judgements if x.rubric_item_id == it.id), None)
                    row[f"{it.id} {it.name}"] = j.verdict if j else "—"
                runs.append(row)
            except Exception as e:
                runs.append({"第N次": i, "总分": None, "备注": f"失败：{str(e)[:100]}"})
        prog.empty()
        note.empty()
        st.session_state["repeat_rows"] = runs
        st.success(f"{n} 次评阅完成")

    runs = st.session_state.get("repeat_rows")
    if not runs:
        return
    df = pd.DataFrame(runs)
    st.dataframe(df, use_container_width=True, hide_index=True)

    ok = [r for r in runs if r.get("总分") is not None]
    if len(ok) >= 2:
        tot = [r["总分"] for r in ok]
        # 逐评分点：取该点出现次数最多的判定，算它占的比例，再对所有点平均
        rates = []
        for key in [k for k in ok[0] if k not in ("第N次", "总分", "耗时s", "待复核", "备注")]:
            vs = [r.get(key) for r in ok if r.get(key)]
            if not vs:
                continue
            rates.append(max(vs.count(v) for v in set(vs)) / len(vs))
        consist = round(100 * sum(rates) / len(rates), 1) if rates else 0.0
        c1, c2, c3 = st.columns(3)
        c1.metric("总分极差", round(max(tot) - min(tot), 1))
        c2.metric("评分点判定稳定率", f"{consist}%")
        c3.metric("满分一致次数", f"{sum(1 for t in tot if t == max(tot))}/{len(tot)}")
        st.caption("这个稳定率不是「调」出来的：若明显偏低，说明该评分点的表述还有歧义，"
                   "正确的动作是改评分点的定义，而不是去改已经落地的判定结果——"
                   "这正是 G 阶段把不确定项转人工复核的理由。")
        st.download_button("下载重复测试 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           "repeat_results.csv", "text/csv")


# ---------------- 入口 ----------------
def render(samples_dir, results_dir, sample_note="", custom_rubric=None):
    st.subheader("批量测试")
    st.caption("跑多份报告看区分度，跑同一份多次看稳定性 —— 两种都可以导出留痕。")

    if demo_mode():
        st.warning("DEMO_MODE 已开启：批量测试不会调用真实模型，结果无意义。")
    if sample_note:
        st.caption(sample_note)
    if not os.path.isdir(samples_dir):
        st.info("云端演示样本目录不可用，请用「评阅」页上传文件。")
        return
    files = sorted(f for f in os.listdir(samples_dir) if f[:1] in "Ss")
    if not files:
        st.warning("样本目录为空。")
        return

    mode = st.radio("测试类型", ["多份报告批量评阅", "同一份重复评阅（一致性测试）"],
                    horizontal=True)
    src = st.radio("评分标准来源",
                   ["内置固定 5 项标准（与人工 gold 对齐）", "使用「评分点」页当前的评分点"],
                   horizontal=True)
    use_fixed = src.startswith("内置")
    rubric = fixed_rubric() if use_fixed else custom_rubric
    if rubric is None or not getattr(rubric, "items", None):
        st.info("尚未生成评分点，请到「评阅」页先点「① 生成评分点」；或直接改用内置固定标准。")
        return
    enable_recheck = st.checkbox("开启 G 阶段一致性复核（调用量翻倍、更慢更稳）", value=False)
    st.markdown("---")

    if mode.startswith("多份"):
        _render_multi(samples_dir, rubric, use_fixed, enable_recheck, files)
    else:
        _render_repeat(samples_dir, rubric, enable_recheck, files)

    # 透明化：让用户看见「有多少次模型输出是靠修复才救回来的」
    s = get_stats()
    if s.get("calls"):
        st.caption(f"本次会话累计：模型调用 {s['calls']} 次，调用失败 {s['failed']} 次，"
                   f"其中 {s.get('json_repaired', 0)} 次的输出靠 JSON 修复才解析成功。")
