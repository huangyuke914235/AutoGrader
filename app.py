# -*- coding: utf-8 -*-
"""AutoGrader · 实验报告智能评阅平台（Streamlit 界面）

启动：  streamlit run app.py

六个标签页：
  tab1 评阅   —— 上传报告 / 选样本 / 输入评分标准 / 跑流水线（含原始版面对照）
  tab2 详情   —— 原文高亮对照 + 逐评分点卡片 + 人工覆盖改分
  tab3 评分点 —— 原子化结果预览与分值编辑（改完必须合计正好 100）
  tab4 导出   —— 成绩表 CSV / 结果 JSON（含 AI 分、最终分与改分记录）
  tab5 批量测试 —— 多份批量评阅 / 同一份重复评阅（一致性）
  tab6 学生自检 —— 不依赖 rubric 的 12 项质量体检（形成性反馈，不产出成绩）
"""
import os
import re
import json
import time
import uuid
import tempfile
import datetime

import streamlit as st
import pandas as pd

import parser as P
import prompts
import batch_ui
import providers
import chrome_i18n
import selfcheck as SC
import history as HIST
import report_pdf
from models import Rubric, RubricItem
from pipeline import (run_grading, stage_rubric, rubric_source_hash,
                      validate_rubric, RubricError, apply_override,
                      effective_score, recompute_total, build_export_rows,
                      DEFAULT_JUDGE_WORKERS)
from llm import (demo_mode, ai_ready, ai_blocked_reason, diagnose,
                 temperature_allowed)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _safe_display_name(name: str) -> str:
    """只保留用于展示的安全文件名：去路径、去控制字符、限制长度"""
    base = os.path.basename(name or "")
    base = re.sub(r"[\x00-\x1f/\\]", "", base)
    return base[:60] or "未命名报告"


_UPLOAD_CACHE = {}          # 只留最近一次上传的结果，避免每次交互都重新落盘 + 重渲染


def _fmt_stamp(iso: str) -> str:
    """ISO 时间戳 → 人读得懂的「09-24 22:05」。"""
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return (iso or "")[:16].replace("T", " ")


def _render_selfcheck_archive(cur_key: str, root: str = None):
    """体检档案：跨会话回看 / 删除 / 导出带走。

    「存了什么、存在哪、怎么删」必须写在界面上 —— 这里放的是学生自己的作业数据，
    悄悄留在他不掌握的目录里、又不给出删除的出口，等于偷偷留存。
    """
    root = root or HIST.HISTORY_ROOT
    with st.expander("📚 我的体检档案（跨会话回看）", expanded=False):
        st.caption("存档**只含检查结论**（分项得分、问题定位、改进方向），"
                   "**不含报告正文、也不含任何密钥**。")
        st.caption("🔒 **每人一个独立空间**：别人看不到你的档案。"
                   "⚠️ 服务器**不保证长期保留**（免费云主机休眠或重新部署后可能清空），"
                   "重要的记录请用下面的「导出」自己存一份。")
        _msg = st.session_state.pop("sc_arch_msg", None)
        if _msg:
            st.caption(_msg)
        # 导入必须放在「有没有存档」的判断**之前**：档案全空的时候恰恰最需要它
        #（换设备、清了网址、服务器清过一次 —— 都是靠导入把记录找回来）。
        _up = st.file_uploader("📥 导入档案（之前导出的 JSON）", type=["json"],
                               key="sc_arch_import")
        if _up is not None and st.session_state.get("_sc_imported") != _up.file_id:
            # 先记 file_id 再干活：Streamlit 每次 rerun 都会把上传重放一遍，
            # 不设这个闸就会一次上传被导入无数条。
            st.session_state["_sc_imported"] = _up.file_id
            _n, _err = HIST.import_bundle(
                _up.getvalue().decode("utf-8", "ignore"), root)
            if _err:
                st.error(_err + "　（正确的做法：回到原来那台机器重新「导出」一份）")
            else:
                st.session_state["sc_arch_msg"] = f"已导入 {_n} 条体检记录。"
                st.rerun()
        idx = HIST.report_index(root)
        if not idx:
            st.caption("还没有存档 —— 体检一次之后，这里会留下记录。")
            return
        by_key = {it["key"]: it for it in idx}
        keys = [it["key"] for it in idx]
        default = cur_key if cur_key in by_key else keys[0]
        pick = st.selectbox(
            "档案", keys, index=keys.index(default), key="sc_arch_pick",
            format_func=lambda k: f"{by_key[k]['name']}（{by_key[k]['count']} 次体检）")
        entries = HIST.load_report(pick, root)
        for i, e in enumerate(entries):
            obj = HIST.to_result(e)
            c1, c2, c3 = st.columns([5, 1, 1])
            with c1:
                st.caption(f"{_fmt_stamp(e['saved_at'])}　·　"
                           f"**{e['data'].get('total')} 分**"
                           + ("" if obj else "　（格式过期，无法回看）"))
            with c2:
                if obj is not None and st.button("回看", key=f"sc_view_{pick}_{i}"):
                    st.session_state["sc_latest"] = obj
                    st.session_state["sc_viewing"] = {
                        "name": by_key[pick]["name"], "at": e["saved_at"]}
                    # 同步切到这个档案，下面的「版本对比」才会画出它的两次差异；
                    # 走「待选」队列而不是直接赋值，见 tab6 开头的说明。
                    st.session_state["sc_pick_pending"] = by_key[pick]["name"]
                    st.rerun()
            with c3:
                if st.button("删除", key=f"sc_del_{pick}_{i}"):
                    _why = []
                    if HIST.delete_entry(e["path"], history_root=root, reason=_why):
                        st.session_state["sc_arch_msg"] = \
                            f"已删除 {_fmt_stamp(e['saved_at'])} 的记录。"
                        st.rerun()
                    # 「删不掉」和「越界被拒」是两件事，不能都给同一句提示 ——
                    # 把目录只读/文件被占用说成「不在允许范围」，会让人往错的方向查。
                    elif _why and _why[0] != "out_of_root":
                        st.error("删除失败：文件正被其他程序占用，或服务器目录不可写。"
                                 f"（系统信息：{_why[0]}）")
                    else:
                        st.error("删除被拒绝：该记录不在允许的目录范围内。")
        if st.button("🗑 清空该档案", key="sc_arch_clear"):
            n = HIST.clear_report(pick, root)
            st.session_state["sc_arch_msg"] = \
                f"已清空「{by_key[pick]['name']}」的 {n} 条记录。"
            if st.session_state.get("sc_latest") and pick == cur_key:
                st.session_state.pop("sc_latest", None)
                st.session_state.pop("sc_viewing", None)
            st.rerun()
        # 导出：把档案交回给本人。免费云主机的磁盘是临时的，
        # 换浏览器 / 清了网址 / 应用休眠都可能让服务器上的副本消失，
        # 只有学生自己手里的这份是可靠的。
        st.download_button(
            "⬇️ 导出全部档案（JSON，可换设备导入）",
            HIST.export_bundle(root),
            f"体检档案_{datetime.date.today().isoformat()}.json",
            "application/json", key="sc_arch_export")


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
        full_text, sections, raw_text = P.parse_file(tmp, keep_lines=True)
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
    value = (full_text, sections, "UP-" + uuid.uuid4().hex[:8], raw_text,
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
    full_text, sections, raw_text = load_text(pick)
    rid = os.path.splitext(pick)[0]
    images, total_pages = [], 0
    orig = os.path.join(ROOT, "data", "raw", rid + ".pdf")
    if os.path.exists(orig):
        try:
            images = _render_cached(orig, os.path.getmtime(orig), P.PREVIEW_MAX_PAGES)
            total_pages = P.pdf_page_count(orig)
        except Exception as e:
            print(f"[warn] 样本版面渲染失败（不影响评阅）：{type(e).__name__}: {e}")
    return full_text, sections, rid, raw_text, images, total_pages


@st.cache_data(show_spinner=False, max_entries=8)
def _grading_pdf_cached(res_json: str, course: str) -> bytes:
    """按「结果内容 + 课程」缓存成绩单 PDF 字节。

    为什么必须缓存：Streamlit 会把**所有 tab 的代码都跑一遍**（不只是当前选中的那个），
    不缓存的话，学生在任何一个页面上点任意一下，这里都会重排一次 PDF ——
    长报告要几百毫秒，纯属白烧，还会让界面发顿。
    """
    from models import GradingResult
    return report_pdf.build_grading_pdf(
        GradingResult.model_validate_json(res_json), course=course)


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

_SID_RE = re.compile(r"^[0-9a-f]{8,64}$")


def _public_host() -> bool:
    """是否跑在公网托管（Streamlit Community Cloud 等）上。

    判据两条，取其一：
      ① 环境变量 AG_PUBLIC=1 显式声明（想让本地也照公网口径跑时用）；
      ② 检测到 Streamlit Cloud 的挂载目录 /mount/src。
    用途只有一个 —— **决定要不要把学生的东西往服务器磁盘上写**。
    本地自用写盘没问题，公网写盘就是替别人保管数据，出事算我们的。
    """
    flag = (os.environ.get("AG_PUBLIC") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return os.path.isdir("/mount/src")


def _archive_root() -> str:
    """当前访问者的独立档案目录。

    为什么必须按人分：所有访问者共用一个 `data/history/` 时，
    A 同学能在档案列表里直接看到 B 同学的报告名和分数。
    单机自用看不出来，一对外开放就是隐私事故。

    编号放在网址里（`?sid=...`）而不是 session_state：刷新页面会重建会话、
    存在 session_state 里的编号会变，档案就找不回来了；网址刷新后还在。
    编号是从 URL 读来的**不可信输入**，必须严格校验成十六进制，
    否则 `?sid=../../etc` 就成了任意路径写入。
    """
    raw = st.query_params.get("sid")
    sid = raw if isinstance(raw, str) and _SID_RE.match(raw) else ""
    if not sid:
        sid = uuid.uuid4().hex[:16]
        try:
            st.query_params["sid"] = sid
        except Exception:          # 某些嵌入环境不允许改地址栏 —— 退回本次会话内有效
            pass
    return HIST.session_root(sid)


@st.cache_resource(show_spinner=False)
def _sweep_archive_spaces() -> int:
    """每个进程启动时清一次长期没人动的档案空间（默认 7 天）。

    公网部署下每人一个目录，没人来收就一直堆在服务器磁盘上。
    清扫失败（只读磁盘、沙箱拦截）一律忽略 —— 它是锦上添花，不能弄崩启动。
    """
    try:
        return HIST.sweep_stale(HIST.HISTORY_ROOT, max_age_days=7)
    except Exception:
        return 0


_sweep_archive_spaces()


def _csv_safe(v):
    """CSV 公式注入防护：以 = + - @ 开头的单元格前面加单引号"""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s

st.set_page_config(page_title="AutoGrader · 实验报告智能评阅", page_icon="📋", layout="wide")

# 右上角 Deploy / 三点菜单 / 设置对话框等框架自带英文，统一汉化（见 chrome_i18n.py）
chrome_i18n.inject()

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
    # keep_lines：学生自检的行级规则（章节定位 / 编号步骤 / 数据行）需要保留换行的原文
    return P.parse_file(os.path.join(SAMPLES, sample), keep_lines=True)


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
    """把评阅结果写到 data/results/，**公网部署时一律不写**。

    为什么公网不能写：GradingResult 里带着**逐字引用的原文证据**，
    落盘等于把学生报告的片段留在所有人共用的服务器磁盘上。
    本地自用无所谓（本来就是自己的机器），公网托管不行 —— 那是我们在替别人保管数据。
    返回空串表示"没写"，调用方据此改话术，不能对着空气说"已保存到 xxx"。
    """
    if _public_host():
        return ""
    os.makedirs(RESULTS, exist_ok=True)
    p = os.path.join(RESULTS, f"{name}.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(res.model_dump_json(ensure_ascii=False, indent=2))
    return p


def render_llm_picker():
    """侧边栏模型接入选择器。返回 llm_cfg dict，交给**两条链路**显式传递：
    评阅（run_grading）与学生自检（run_selfcheck）。

    三条安全纪律（别为了省事破坏）：
    1. 密钥只存 st.session_state，**不落盘、不进日志、不进导出文件**；
    2. 绝不写模块级全局变量 —— Streamlit 多会话共享模块，那会让 A 同学的
       密钥被 B 同学的会话用到；
    3. 回显一律脱敏（providers.mask_key）。
    """
    names = [p["name"] for p in providers.PROVIDERS]
    if providers.shared_available():
        names.insert(1, "平台提供额度（免费·学生免配置）")
    picked = st.selectbox("通道", names, key="llm_channel_name")

    if picked == "平台提供额度（免费·学生免配置）":
        cfg = providers.shared_cfg()
        left = providers.daily_limit() - int(st.session_state.get("shared_used", 0))
        if left <= 0:
            st.warning("今日平台额度已用完，本次将只用离线规则（8 项）。")
            return providers.build_cfg("offline")
        st.caption(f"平台承担费用，本次会话剩余 {max(left, 0)} 次。"
                   f"用完自动回落离线规则，不会中断。")
        return cfg

    pid = next(p["id"] for p in providers.PROVIDERS if p["name"] == picked)
    p = providers.get(pid)

    if pid == "offline":
        st.caption("默认通道。8 项规则 / 58 权重，零配置零成本、结果可复现。")
        st.caption("查不了：原理是否抄书、计算是否跳步、误差是否套话、结论有无依据。")
        return providers.build_cfg("offline")

    st.caption(p["note"])

    if pid == "custom":
        base_url = st.text_input("接口地址 Base URL",
                                 value=st.session_state.get("llm_base_url", ""),
                                 placeholder="https://your-host/v1", key="llm_bu")
        model = st.text_input("模型名", value=st.session_state.get("llm_model", ""),
                              placeholder="qwen2.5-72b-instruct", key="llm_mdl")
    else:
        base_url, model = p["base_url"], p["model"]
        st.caption(f"接口 `{base_url}`　模型 `{model}`")

    if p["needs_key"]:
        key = st.text_input("API Key", type="password",
                            value=st.session_state.get("llm_api_key", ""),
                            key="llm_key_input",
                            help="只保存在你本次会话里，不会上传、不会写入任何文件")
        if p["key_url"]:
            st.caption(f"[去申请密钥]({p['key_url']})")
    else:
        key = p.get("placeholder_key", "")

    cfg = providers.build_cfg(pid, key, base_url, model)
    why = providers.validate(cfg)
    if why:
        st.warning(f"{why} —— 这 4 项会显示「未检测」，不会当成 0 分。")
    else:
        st.success(f"已就绪：{providers.describe(cfg)}", icon="✅")
        # 只把非敏感部分写进 session（key 由 st.text_input 自己保管在 widget state 里）
        st.session_state["llm_base_url"] = base_url
        st.session_state["llm_model"] = model
        # 为什么把「测试连接」放在这里而不是评阅页 + 智能失败时才提示：
        # 换一家供应商时，人是**心虚**的 —— 他不知道填的 Key 到底能不能用，
        # 也不知道跑不动时该怪谁。与其等第一次评阅失败再回溯，
        # 不如在配置完成的当下就给一个确定的答案。
        if st.button("🔌 测试连接", key="btn_diag", use_container_width=True,
                     help="真实调用一次：验 Key、列可用模型、验 JSON 强输出。只花极少 token"):
            with st.spinner("正在连通测试…"):
                st.session_state["llm_diag"] = diagnose(
                    api_key=cfg.get("api_key"),
                    base_url=cfg.get("base_url"),
                    model=cfg.get("model"),
                )

    _d = st.session_state.get("llm_diag")
    if _d:
        _render_diag(_d)
    return cfg


def _render_diag(d: dict):
    """把诊断结果画出来。

    排版纪律：这份结果**必须能被整段选中复制**出去贴给人求助 ——
    所以它用纯文本 + 代码块组织，不用需要逐个展开才能看全的控件；
    同时任何密钥原文都在 llm._strip_secret 里被换成了掩码。
    """
    if d.get("ok"):
        st.success("✅ 通道可用，可以直接开跑。")
    else:
        st.error("❌ 通道不可用，按下面逐步排查：")
    lines = []
    for name, ok, detail in d.get("steps", []):
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} {name}" + (f"　{detail}" if detail else ""))
    if lines:
        st.code("\n".join(lines), language="text")
    if d.get("hint"):
        st.info(d["hint"])
    if d.get("models"):
        with st.expander(f"这个 Key 可以用的模型（{len(d['models'])} 个，点开复制）"):
            st.code("\n".join(d["models"]), language="text")


# ---------- 侧边栏 ----------
with st.sidebar:
    st.markdown("### AutoGrader")
    st.caption("RAG-E 四阶判定链")
    st.markdown("---")
    st.markdown("**模型接入**")
    llm_cfg = render_llm_picker()

    # 状态行必须描述「接下来真的会发生什么」。
    # 旧实现是两行各自正确的废话：上面一行显示环境变量里的模型（与刚选的通道无关），
    # 下面一行是 DEMO_MODE 警告（不管用户有没有自带密钥）。合起来会让人得出
    # 错误结论 —— 填完 key 点评阅被拦，就是这个组合造成的。
    _kw = providers.llm_kwargs(llm_cfg)
    if llm_cfg is not None and llm_cfg.get("id") == "offline":
        st.warning("离线规则通道：只体检 8 项规则（58 权重）。"
                   "「① 评阅」页需要模型通道，请在上方选一个。")
    elif _kw:
        st.caption("✅ 评阅与自检都会用上面这个通道调用模型。")
        if demo_mode():
            st.caption("服务端 DEMO_MODE=true，但你自带的密钥会直接放行，不再拦截。")
    elif demo_mode():
        st.warning("DEMO_MODE 已开启且未接入模型：评阅只会返回预置演示结果"
                   "（需先在服务端跑 tools/make_demo.py 生成）。")
    else:
        st.info("未接入模型：评阅会使用服务端 .env 里的平台配置。")

    st.markdown("---")
    st.markdown("**三条铁律**")
    st.caption("① 分数由代码加总，模型只输出单点判定")
    st.caption("② 无证据的判断不算数，引用必须原文匹配")
    st.caption("③ 不确定就交给人，标记待复核")

st.title("AutoGrader · 实验报告智能评阅平台")
st.caption("把老师的评分标准变成可核查、可溯源、可校准的判定流水线")

sweep_stale_uploads()      # 兜底清扫超期上传残留（正常路径都会在 finally 里删掉）

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["① 评阅", "② 详情对照", "③ 评分点", "④ 导出", "⑤ 批量测试", "⑥ 学生自检"])

# ---------- 教师端 ①~⑤：常显 ----------
# v3 起按用户要求恢复常显：老师实际打分是 100 分制，学生端保留教师入口。
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
                full_text, sections, report_id, _raw, imgs, total_pages = load_sample(pick)
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
                        (full_text, sections, report_id, _raw, disp,
                         imgs, total_pages) = load_uploaded(up)
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
        # 绑 key 是为了让「④ 导出」那页也能读到它 —— 成绩单上要印课程/背景，
        # 而它俩在不同的 tab 里，局部变量传不过去。
        course = st.text_input("课程 / 实验背景", value="计算机专业课程实验",
                               key="course_input")

    # 在按钮上方拦一道：让用户点之前就知道点了会失败，而不是等报错
    if llm_cfg is not None and llm_cfg.get("id") == "offline":
        st.warning("当前是「仅离线规则」通道，而评阅链路需要大模型。"
                   "请在左侧「模型接入」里选一个模型通道；"
                   "只想做规则体检请用「⑥ 学生自检」。")

    # 速度开关：Kimi 这类"会思考"的模型单次调用就要好几秒，
    # 慢不慢主要由「调用次数」决定 —— 把次数摆到界面上，让用户自己取舍，别让他干等。
    with st.expander("⚡ 速度与质量（可选）", expanded=False):
        do_recheck = st.checkbox(
            "开启 G 阶段一致性复核（每个评分点多跑一次模型，耗时约翻倍）",
            value=st.session_state.get("opt_recheck", False),
            key="opt_recheck",
            help="复核会用第二遍调用检查「判定」与「原文证据」是否自洽，"
                 "能抓出少量误判；关掉它就只剩一次判定，快一倍。")
        judge_workers = st.slider(
            "A 阶段并发路数", min_value=1, max_value=8,
            value=st.session_state.get("opt_workers", DEFAULT_JUDGE_WORKERS),
            key="opt_workers",
            help="同时判定几个评分点。路数越多越快，但不是越高越好："
                 "并发太高会撞平台的每分钟请求数限制（429），退避重试反而更慢。")
        _n = len(st.session_state.get("rubric").items) if st.session_state.get("rubric") else 0
        if _n:
            _calls = _n * (2 if do_recheck else 1) + 2     # +2：反馈总结 & （未生成时）评分点
            st.caption(f"当前 {_n} 个评分点 → 预计模型调用 **约 {_calls} 次**"
                       f"（并发 {judge_workers} 路，实际耗时还要看模型单次响应速度）")
        else:
            st.caption("生成评分点后会显示预计调用次数。")

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
                rub = stage_rubric(raw_rubric, course, llm_cfg=llm_cfg)
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
                              rubric=rub, progress=cb, llm_cfg=llm_cfg,
                              enable_recheck=st.session_state.get("opt_recheck", False),
                              judge_workers=st.session_state.get(
                                  "opt_workers", DEFAULT_JUDGE_WORKERS))
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
        # 用 .get 而不是硬下标：结果与原文是成对写入的，眼下不会缺。
        # 但一旦缺了（比如将来给评阅结果也做「回看历史」，只还原结果、没还原原文），
        # 硬下标会把整个「详情」页打崩，而这里其实只是少显示一块内容。
        ft = st.session_state.get("full_text") or ""
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
            if ft:
                st.markdown(f"<div class='txtbox'>{highlight(ft, all_quotes)}</div>",
                            unsafe_allow_html=True)
            else:
                st.caption("本次会话里没有留存报告原文，因而无法在原文上高亮证据位置。"
                           "重新评阅一次即可看到；这不影响右侧的逐项判定。")
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
        st.caption("这是人工确认与校准的关口：可以改分值、也可以**增删**评分点。"
                   "改完必须点下方「校验并应用」，之前的结果会作废。")
        # 归一化成 Rubric 再改。原实现在 Rubric 和 GradingResult.items(list) 两个来源之间
        # 游走，增删后要写回 session_state 就会分叉 —— 这里一次性统一。
        rub_obj = rub if isinstance(rub, Rubric) else Rubric(items=list(rub))
        st.session_state["rubric"] = rub_obj
        items = rub_obj.items

        _to_del = None
        for it in list(items):
            c1, c2, c3, c4 = st.columns([2, 1, 3, 0.4])
            c1.markdown(f"**{it.id} {it.name}**")
            v = c2.number_input("分值", 0.0, 100.0, float(it.max_score), step=1.0,
                                key=f"score_{it.id}", label_visibility="collapsed")
            it.max_score = v
            c3.caption(it.criteria or "（未写判定标准）")
            if c4.button("删", key=f"del_{it.id}", help=f"删除评分点「{it.name}」"):
                _to_del = it.id

        if _to_del:
            rub_obj.items = [x for x in items if x.id != _to_del]
            st.session_state["result"] = None
            st.rerun()

        with st.expander("＋ 新增评分点"):
            with st.form("form_add_item", clear_on_submit=True):
                _n = st.text_input("名称", placeholder="例如：误差分析是否有依据")
                _c = st.text_input("判定标准", placeholder="例如：指出误差来源并给出量化的估计")
                _s = st.number_input("分值", 0.0, 100.0, 10.0, step=1.0)
                _ok = st.form_submit_button("加入评分点")
            if _ok:
                _name = (_n or "").strip()
                if not _name:
                    st.error("名称不能为空 —— 评分点要让人一眼看懂它在查什么。")
                else:
                    # id 不能与现有项撞车：撞了会让两行共用同一个 number_input key，
                    # 改一个分值另一个跟着变，而且事后查不出为什么。
                    used = {x.id for x in rub_obj.items}
                    k = 1
                    while f"u{k}" in used:
                        k += 1
                    rub_obj.items.append(RubricItem(
                        id=f"u{k}", name=_name,
                        criteria=(_c or _name).strip(), max_score=float(_s)))
                    st.session_state["result"] = None
                    st.rerun()

        total = sum(float(x.max_score) for x in rub_obj.items)
        st.markdown("---")
        st.markdown(f"**合计分值：{round(total, 1)}**"
                    + (f"　⚠ 满分应合计 100，当前差 {round(100 - total, 1)}，"
                       f"请在各评分点之间重新分配（删除或新增后通常需要这一步）"
                       if abs(total - 100) >= 0.01 else "　✅"))
        if st.button("校验并应用这套评分点"):
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
        # 可打印成绩单。这份单子会离开这个页面（打印、转发、截图），
        # 所以「仅供参考、以教师为准」必须印在纸上 —— 见 report_pdf 的模块说明。
        try:
            st.download_button(
                "下载 PDF 成绩单（可直接打印）",
                _grading_pdf_cached(res.model_dump_json(),
                                    st.session_state.get("course_input") or ""),
                f"{res.report_id}_评阅成绩单.pdf", "application/pdf")
        except Exception as e:
            # 导出这条链不该因为一个附件而整个塌掉：CSV / JSON 必须仍然可用
            st.warning(f"PDF 成绩单暂时生成不了（{type(e).__name__}）：{e}\n\n"
                       "CSV / JSON 不受影响，仍可正常下载。")
        if res.run_info:
            with st.expander("本次评分的可审计信息（用于事后复现）"):
                st.json(json.loads(res.run_info.model_dump_json()))
        if _public_host():
            # 不能对着空气说"已保存到 xxx"：公网部署下根本没写盘
            st.caption("🌐 公网部署：**评阅结果不写入服务器磁盘**"
                       "（结果里含原文引用，我们不该替你留存）。"
                       "需要留档请用上面的 CSV / JSON / PDF 下载。")
        else:
            st.caption(f"结果同时已保存至 data/results/{res.report_id}.json"
                       f"（含 AI 原始分、人工改分记录与运行元信息）")

# ---------- tab5 批量测试 ----------
with tab5:
    batch_ui.render(SAMPLES, RESULTS, _SAMPLE_NOTE,
                    custom_rubric=st.session_state.get("rubric"))

# ---------- tab6 学生自检 ----------
# 与 tab1 的两点本质区别，界面上必须说清楚，否则会被当成「第二个评分入口」：
#   ① 不读教师的 rubric，用的是一套通用实验报告规范；
#   ② 产出的是改进方向，不是成绩 —— 分数只是让学生看到自己进步幅度的刻度。
with tab6:
    st.subheader("学生自检 · 实验报告体检")
    st.caption("不依赖评分标准的通用体检：12 项检查，只给问题定位与改进方向。"
               "**这不是成绩**，学生可以改完再传一次，看自己进步了多少。")
    # 档案读写一律带上「我自己的目录」：不传就是退回全局共享目录，
    # 公网部署下那是把一个人的作业数据摊给所有人看。
    _AR = _archive_root()

    sc_left, sc_right = st.columns([1, 1])
    with sc_left:
        sc_mode = st.radio("报告来源", ["内置脱敏样本", "上传文件"],
                           horizontal=True, key="sc_mode")
        sc_text, sc_sections, sc_id, sc_raw = "", [], "", ""
        sc_disp = ""
        if sc_mode == "内置脱敏样本":
            sc_files = sorted(f for f in os.listdir(SAMPLES) if f.startswith("S")) \
                if os.path.isdir(SAMPLES) else []
            if not sc_files:
                st.warning("未找到内置样本，请改用「上传文件」")
            else:
                if _SAMPLE_NOTE:
                    st.caption(_SAMPLE_NOTE)
                sc_pick = st.selectbox("样本", sc_files, key="sc_pick")
                sc_text, sc_sections, sc_id, sc_raw, _imgs, _pages = load_sample(sc_pick)
                sc_disp = os.path.splitext(sc_pick)[0]
        else:
            st.warning("上传的报告正文会被发送到本项目配置的模型服务（见侧边栏显示的模型）。"
                       "**请勿上传含真实姓名/学号的未脱敏作业。**")
            sc_up = st.file_uploader("上传 PDF / DOCX / TXT（≤ 20MB）",
                                     type=["pdf", "docx", "txt", "md"], key="sc_up")
            if sc_up:
                if sc_up.size and sc_up.size > MAX_UPLOAD_BYTES:
                    st.error(f"文件过大（{sc_up.size/1048576:.1f}MB），上限 20MB。")
                else:
                    try:
                        (sc_text, sc_sections, sc_id, sc_raw, sc_disp,
                         _imgs, _pages) = load_uploaded(sc_up)
                        sc_disp = os.path.splitext(sc_disp)[0]
                    except ValueError as e:
                        st.error(f"上传被拒绝：{e}")
                        sc_text, sc_sections, sc_id, sc_raw = "", [], "", ""
                    except Exception as e:
                        st.error(f"解析失败（{type(e).__name__}）：{e}")
                        sc_text, sc_sections, sc_id, sc_raw = "", [], "", ""

        # 档案名是「两次体检能不能对比上」的唯一凭据。
        # 拿 report_id 不行：上传文件的 id 是随机 UUID，同一篇报告前后两版
        # 会拿到两个不同的 id，跨会话的趋势就永远连不上。
        # 所以这里优先给「选已有档案」，而不是只给一个输入框 ——
        # 跨会话对比不该靠学生记住自己上次打了什么字。
        _idx = HIST.report_index(_AR)
        _opts = ["＋ 新档案…"] + [it["name"] for it in _idx]
        # 程序化改选档案（刚保存完 / 从档案面板点了「回看」）不能在同一个 run 里
        # 给控件赋值 —— Streamlit 会抛 StreamlitAPIException 把整个脚本打断。
        # 所以只登记「待选」，等下一轮、在这个下拉框实例化**之前**再落地；
        # 且必须先确认它在选项里，否则会给 selectbox 塞一个不存在的值。
        _pending = st.session_state.pop("sc_pick_pending", None)
        if _pending in _opts:
            st.session_state["sc_pick_label"] = _pending
        _sel = st.selectbox(
            "档案名（决定两次体检能否对比上）", _opts, key="sc_pick_label",
            help="「初稿 → 改完再测」必须落进**同一个档案名**下，系统才认得出是同一篇报告。"
                 "改完再测时请在这里选上次那个名字。")
        sc_label = st.text_input("新档案名", value=sc_disp or sc_id or "",
                                 key="sc_new_label") if _sel == "＋ 新档案…" else _sel
        sc_archive = sc_label or sc_disp or sc_id or "未命名报告"

    with sc_right:
        sc_exp_sel = st.selectbox(
            "实验类型 / 课程背景",
            SC.UI_OPTIONS, key="sc_exp_sel")
        if sc_exp_sel == "自定义…":
            sc_exp = st.text_input("请输入实验类型", value="", key="sc_exp_custom")
        else:
            sc_exp = sc_exp_sel
        # 章节名因学科而异（计算机报告叫「技术要点」而不是「实验原理」），
        # 词表选错了会把完整报告判成缺章节 —— 所以把判定结果明写出来，让用户能发现
        st.caption(f"章节词表按「{SC.pick_profile(sc_exp)}」匹配，"
                   f"若与实际不符请改选实验类型")
        sc_ai = st.toggle("启用 AI 语义诊断（4 项）", value=True, key="sc_ai")
        rule_w = sum(SC.CHECK_ITEMS[k][0] for k in SC.RULE_ITEMS)
        ai_w = sum(SC.CHECK_ITEMS[k][0] for k in SC.AI_ITEMS)
        st.caption(f"规则引擎负责 {len(SC.RULE_ITEMS)} 项（权重 {rule_w}，离线、零成本、"
                   f"结果可复现）；AI 负责 {len(SC.AI_ITEMS)} 项（权重 {ai_w}）："
                   f"{'、'.join(SC.AI_ITEMS)}。")
        # AI 缺席必须在计分前就说清楚，否则 42% 的权重静默消失，
        # 使用者会把「只跑了 8 项」当成「跑了 12 项」。
        # 判断依据是学生**当前选的通道**，不是平台全局配置。
        if not sc_ai:
            _why = "已关闭 AI 语义诊断"
        elif llm_cfg and llm_cfg.get("id") == "offline":
            _why = "当前是离线规则通道"
        else:
            _why = providers.validate(llm_cfg or {})
            if llm_cfg is None and ai_blocked_reason():
                _why = ai_blocked_reason()
        if _why:
            st.warning(f"**当前只有 {len(SC.RULE_ITEMS)}/{len(SC.CHECK_ITEMS)} 项在计分**："
                       f"{_why}。")
            st.caption(f"AI 的 {len(SC.AI_ITEMS)} 项（{'、'.join(SC.AI_ITEMS)}，共 {ai_w} 分权重）"
                       f"会显示「未检测」并**从总分分母中剔除**，绝不当成 0 分算给学生。"
                       f"因此当前总分只反映「格式与结构」维度，"
                       f"**不代表报告内容的正确性** —— 原理是否讲对、数据处理是否成立、"
                       f"误差分析有无依据、结论是否被数据支撑，这四项都判不了。")
        else:
            st.caption(f"当前通道：{providers.describe(llm_cfg)} —— 12 项全部参与计分。")
            # 必须说清楚的一个取舍：某些型号的温度参数被平台锁死，
            # 系统也就没法把评分锁到可完全复现。与其让学生两次查出不同分数后
            # 怀疑产品有问题，不如在动手之前先把话说完。
            _m = (llm_cfg or {}).get("model") or ""
            if _m and not temperature_allowed(_m):
                st.caption(f"提示：{_m} 的采样温度由平台固定（不可由调用方设置），"
                           f"因此**同一份报告连查两次可能有几分出入**，"
                           f"这是该平台的机制，不是系统出错。"
                           f"自检的价值在于定位问题与观察改进趋势，"
                           f"不必纠结单次的分差；若需要逐次完全一致的结果，"
                           f"请换用 DeepSeek 通道。")
        st.markdown("**自检的三条边界**")
        st.caption("① 只诊断，不代写 —— 改进方向不超过 40 字，不给可抄录的正文")
        st.caption("② 不生成数据、图表、参考文献 —— 缺了就说缺失")
        st.caption("③ 体检分与教师正式评分无关")

    if sc_text:
        st.success(f"已载入 {sc_id}：{len(sc_text)} 字 / {len(sc_sections)} 章节"
                   f"　｜　将存入档案：**{sc_archive}**")

    if st.button("开始体检", type="primary", use_container_width=True, key="sc_run"):
        if not sc_text:
            st.error("请先选择或上传一份报告。")
        else:
            with st.spinner("体检中（规则检查 + AI 诊断）…"):
                sc_res = SC.run_selfcheck(sc_text, sc_sections,
                                          experiment_type=sc_exp,
                                          use_ai=sc_ai, report_id=sc_id,
                                          raw_text=sc_raw, llm_cfg=llm_cfg)
            # 共享额度计数：只在真的用了平台通道时才扣
            if llm_cfg and llm_cfg.get("id") == providers.SHARED_ID \
                    and sc_ai and not sc_res.undetected:
                st.session_state["shared_used"] = \
                    int(st.session_state.get("shared_used", 0)) + 1
            # 落盘而不是只留会话：学生的典型用法是**跨时间**的
            #（今天测初稿、明天改完再测），浏览器一刷新会话就没了，
            # 「看自己进步了多少」这个主用法会直接失效。
            try:
                HIST.save_entry(sc_res, sc_archive, history_root=_AR)
                st.session_state["sc_pick_pending"] = sc_archive
            except OSError as e:
                # 磁盘满 / 只读部署写不进去 —— 不能连带把这次体检结果也弄丢
                st.warning(f"本次结果未能存入本机档案（不影响这次体检的结论）：{e}")
            st.session_state["sc_latest"] = sc_res
            st.session_state.pop("sc_viewing", None)

    sc_res = st.session_state.get("sc_latest")
    _viewing = st.session_state.get("sc_viewing")
    if _viewing:
        st.info(f"正在回看档案「{_viewing['name']}」于 {_fmt_stamp(_viewing['at'])} "
                f"留下的历史记录 —— 这不是最新一次体检的结果。")
        if st.button("← 回到最新一次", key="sc_back_latest"):
            st.session_state.pop("sc_viewing", None)
            st.rerun()
    if not sc_res:
        st.info("体检结果会显示在这里。可以连续体检两版（初稿 → 修改版），在「版本对比」里看进步。")
    else:
        st.markdown("---")
        weak = [it for it in sc_res.items if it.status in ("偏弱", "缺失")]
        passed = [it for it in sc_res.items if it.status == "通过"]
        # counted_w 只用于说明「本次实际测了多少权重」；
        # total 本身已是百分制（compute_total 内部：加权得分 ÷ 已检测权重 × 100），
        # 不需要、也不允许再拿它当分母 —— 那是二次折算。
        counted_w = sum(SC.CHECK_ITEMS[it.name][0] for it in sc_res.items
                        if it.status != "未检测" and it.name in SC.CHECK_ITEMS)
        # 「通过项 / 待改进项」的分母也要用**已检测**项数：
        # 12 项里 4 项没测时，2 通过 + 6 待改进 = 8，写成 x/12 会让人以为
        # 剩下 4 项「既不通过也不待改进」是什么第三态，实际是压根没测。
        checked = [it for it in sc_res.items if it.status != "未检测"]

        m1, m2, m3 = st.columns(3)
        # 老师打分是 100 分制，自检也按百分制输出。
        # compute_total 已经做了用户要的「按百分比换算成 100 分」：
        # 已检测项的加权得分 ÷ 已检测权重 × 100，未检测项从分母剔除、绝不当 0 分。
        m1.metric("体检总分", f"{sc_res.total} / 100",
                  help=f"百分制口径：已检测 {len(checked)} 项（共 {counted_w} 权重）的"
                       f"加权平均折算为 100 分制。未检测项已从分母剔除，绝不当 0 分计。")
        m2.metric("通过项", f"{len(passed)} / {len(checked)}")
        m3.metric("待改进项", f"{len(weak)}")
        if sc_res.undetected:
            st.caption(f"本次实际只测了 **{len(checked)}/{len(SC.CHECK_ITEMS)} 项**"
                       f"（权重 {counted_w}/100）：{len(sc_res.undetected)} 项未检测"
                       f"已从分母剔除，绝不当 0 分 —— {'、'.join(sc_res.undetected)}。"
                       f"总分按已检测部分的**百分比折算为 100 分制**输出。")
            st.caption("⚠️ 因此这个分数只覆盖「格式与结构」维度，**不覆盖内容正确性**"
                       "（原理是否讲对、数据处理是否成立、误差有无依据、结论是否被数据支撑）。")

        sc_tab_a, sc_tab_b, sc_tab_c = st.tabs(["分项得分", f"待改进问题（{len(weak)}）", "版本对比"])

        with sc_tab_a:
            for it in sc_res.items:
                w = SC.CHECK_ITEMS.get(it.name, (0, ""))[0]
                c1, c2, c3, c4 = st.columns([3, 1, 5, 1])
                with c1:
                    st.markdown(f"**{it.name}**")
                with c2:
                    st.caption(f"权重 {w}%")
                with c3:
                    st.progress(min(max(it.score, 0), 100) / 100)
                with c4:
                    st.caption(f"{int(it.score)}")
                st.caption(f"状态：{it.status}｜引擎：{it.engine}"
                           + ("｜" + it.issues[0].problem if it.issues else ""))
            # 12 项清单（列类型统一转成字符串，避免 pyarrow 因混合类型报错）
            rows = [{
                "检查项": name,
                "权重": f"{w}%",
                "引擎": {"rule": "规则", "ai": "AI"}[engine],
                "状态": next((it.status for it in sc_res.items if it.name == name), "未检测"),
                "得分": str(int(next((it.score for it in sc_res.items if it.name == name), 0))),
            } for name, (w, engine) in SC.CHECK_ITEMS.items()]
            with st.expander("12 项检查清单（权重与引擎）"):
                st.table(rows)

        with sc_tab_b:
            if not weak:
                st.success("没有发现明显问题。建议再人工核对一遍单位、有效数字与图表标题后提交。")
            for i, it in enumerate(sorted(weak, key=lambda x: (
                    {"缺失": 0, "偏弱": 1}.get(x.status, 2),
                    -SC.CHECK_ITEMS.get(x.name, (0, ""))[0])), 1):
                with st.container(border=True):
                    st.markdown(f"**{i}. {it.name}**　·　{it.status}　·　"
                                f"权重 {SC.CHECK_ITEMS.get(it.name, (0, ''))[0]}%")
                    for iss in it.issues:
                        if iss.problem:
                            st.caption("问题：" + iss.problem
                                       + (f"（{iss.location}）" if iss.location else ""))
                        if iss.direction:
                            st.markdown(f"↳ 改进方向：{iss.direction}")

        with sc_tab_c:
            # 数据源是本机档案而不是 session_state —— 会话一断（刷新、换设备）
            # 内存里的历次记录就没了，而这里要的恰恰是「隔几天再测一次」的跨会话对比。
            _entries = HIST.load_report(HIST.safe_name(sc_archive), _AR)
            if len(_entries) < 2:
                st.info("再体检一次（比如先测初稿、改完再测一次），这里会显示逐项的分差、"
                        "修复情况与进步折线。**两次必须用同一个档案名**"
                        f"（当前：{sc_archive}），否则会被当成两篇不同的报告。")
            else:
                # 趋势线：分项表回答「这次具体改对了什么」，折线回答「整体在不在往上走」。
                # 两个问题不一样，所以都要在。三次以上体检时折线才真正有信息量。
                _t = HIST.trend(HIST.safe_name(sc_archive), _AR)
                if len(_t) >= 2:
                    st.markdown("**体检总分趋势**")
                    st.line_chart(pd.DataFrame(
                        {"体检总分": [v for _, v in _t]},
                        index=[k for k, _ in _t]))
                    st.caption(f"共 {len(_t)} 次体检，从 "
                               f"{_t[0][1]} 分到 {_t[-1][1]} 分"
                               f"（{_t[-1][1] - _t[0][1]:+.1f}）。")
                    # 这条提示不是客套：某些通道的温度由平台锁死，
                    # 报告一字未改也可能差几分，不看清楚就会把抖动当成退步。
                    if not temperature_allowed((llm_cfg or {}).get("model") or ""):
                        st.caption("⚠️ 当前通道的采样温度由平台固定，**同一份报告连查两次也可能有几分出入**。"
                                   "请看趋势的**方向**，不要纠结单次分差。")
                _prev, _cur = HIST.to_result(_entries[-2]), HIST.to_result(_entries[-1])
                if _prev is None or _cur is None:
                    st.error("历史记录的结构与当前版本不一致，已无法对比。"
                             "不影响新的体检；可在下方档案里删掉这几条旧记录。")
                else:
                    st.caption(f"对比：**{_fmt_stamp(_entries[-2]['saved_at'])}**"
                               f"（{_prev.total} 分）→ "
                               f"**{_fmt_stamp(_entries[-1]['saved_at'])}**"
                               f"（{_cur.total} 分）　档案：{sc_archive}")
                    st.table(SC.diff_results(_prev, _cur))
                    _d = round(_cur.total - _prev.total, 1)
                    if _d > 0:
                        st.success(f"相比上一次 **+{_d} 分**。")
                    elif _d < 0:
                        st.warning(f"相比上一次 **{_d} 分**。")
                    else:
                        st.caption("两次总分持平 —— 可以看上面逐项有没有变化。")
                    st.caption("对比只用于自我改进，不代表教师评分。")

        # 下载文件名带上档案名与时间：同一篇报告前后几版下下来重名，
        # 学生会覆盖掉自己的初稿版本 —— 那正是用来对比的那一版。
        #（Windows 文件名不允许冒号，这里先把 "09-24 22:05" 洗成 "09-24_2205"）
        _when = ("_" + _fmt_stamp(_viewing["at"]).replace(" ", "_").replace(":", "")) \
            if _viewing else ""
        st.download_button("下载体检报告（Markdown）",
                           SC.to_markdown(sc_res),
                           f"{sc_archive or 'report'}{_when}_自检报告.md",
                           "text/markdown", key="sc_dl")
        if st.button("清除当前显示的结果", key="sc_clear"):
            st.session_state.pop("sc_latest", None)
            st.session_state.pop("sc_viewing", None)
            st.rerun()
        _render_selfcheck_archive(HIST.safe_name(sc_archive), _AR)
        st.caption("本模块只做质量诊断与改进方向提示，不生成可抄录的正文；"
                   "体检分为自检参考值，与教师的正式评分无关。")
