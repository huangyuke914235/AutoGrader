# -*- coding: utf-8 -*-
"""可打印成绩单（PDF）—— 零新增依赖

为什么自己排版而不引 PDF 引擎：`requirements.txt` 里已经有 pymupdf（用来解析 PDF），
它内置的 `china-s`（Droid Sans Fallback Regular）能直接写中文，
不必为了一张成绩单再拖进 weasyprint / reportlab 这一整条依赖链。
读 PDF 与写 PDF 必须落在同一个库对象上，所以导入统一走 `parser.import_fitz()`。

写这份单子有一条硬约束：**它不能长得像一份正式成绩**。
AI 的判定只是建议，最终成绩由教师定 —— 所以「仅供参考，教师有最终决定权」
必须出现在第一页顶部，并且**每一页的页脚都带**，而不是藏在末尾一行小字里。
单子是要被打印、被传播的，脱离了页面上下文的截图同样不能让人误会。
"""
from datetime import datetime
import os

from parser import import_fitz

# 内置字体：pymupdf 自带的 CJK 字体，零依赖、永远可用。
BUILTIN_FONT = "china-s"          # Droid Sans Fallback Regular
EMBED_NAME = "ZhSans"             # 内嵌系统字体时用的名字
A4 = (595.28, 841.89)
MARGIN = 46.0
MAX_CHARS = 700                   # 单次投递的字符上限，保证任何一块都塞得进一页
BOTTOM = A4[1] - MARGIN - 26      # 页脚上方才是正文的下边界

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",                 # 微软雅黑：中英混排最稳
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",         # macOS
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # 常见 Linux
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]


def _pick_font() -> str:
    """优先内嵌系统里真装有的中文字体；找不到就返回空串，退回内置字体。

    为什么非要费这一下：pymupdf 内置的 CJK 字体把**西文也当全角**排，
    「deepseek-chat」会被印成「d e e p s e e k - c h a t」（12 字符占 130pt ≈ 1.08em/字），
    一张要交到老师手上的成绩单这样很糟。

    而**不能只依赖系统字体**：云端容器里不一定有中文字体，
    只认系统字体的话部署过去就是满纸方框或空白。所以内置字体是兜底，不是备选。
    """
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return ""

INK = (0.13, 0.13, 0.13)
MUTED = (0.42, 0.42, 0.42)
ACCENT = (0.65, 0.12, 0.12)

_VERDICT = {"hit": "命中", "partial": "部分命中", "miss": "未达"}
_FOOT = "AI 评阅建议稿 · 仅供参考 · 最终成绩由教师评定"


class _Doc:
    """极简排版器：只有「自上而下流式写入」一种模式，够用就好。"""

    def __init__(self):
        self.fitz = import_fitz()
        self.doc = self.fitz.open()
        self.font_path = _pick_font()
        self.font = EMBED_NAME if self.font_path else BUILTIN_FONT
        self.page = None
        self.y = 0.0
        self._new_page()

    def _use_embedded(self):
        """给当前页注册内嵌字体。**每页都要调**（fontfile 是按页绑定的）。

        实测：字体文件不可用时 `insert_font` 会当场抛 `FzErrorLibrary`，不会拖到写文字时才爆，
        所以这里能可靠地接住。接住之后把 `font_path` 清空 —— 退回只发生这一次，
        后面几页进来就 early-return，因此不会出现「前几页一个字体、后几页另一个字体」的错乱。
        """
        if not self.font_path:
            return
        try:
            self.page.insert_font(fontname=EMBED_NAME, fontfile=self.font_path)
        except Exception:
            self.font_path = ""
            self.font = BUILTIN_FONT

    def _new_page(self):
        self.page = self.doc.new_page(width=A4[0], height=A4[1])
        self._use_embedded()
        self.y = MARGIN
        # 页脚逐页写：这单子一旦被打印或截图传播，脱离上下文的某一页
        # 同样不能让人把它当成正式成绩。
        self.page.insert_text((MARGIN, A4[1] - 30), _FOOT, fontname=self.font,
                              fontsize=7.5, color=MUTED)
        self.page.insert_text((A4[0] - MARGIN - 46, A4[1] - 30),
                              f"第 {self.page.number + 1} 页", fontname=self.font,
                              fontsize=7.5, color=MUTED)

    def space(self, h):
        self.y += h
        if self.y > BOTTOM:
            self._new_page()

    def rule(self, color=(0.8, 0.8, 0.8)):
        if self.y + 6 > BOTTOM:
            self._new_page()
        self.page.draw_line((MARGIN, self.y), (A4[0] - MARGIN, self.y),
                            color=color, width=0.7)
        self.y += 8

    def text(self, s, size=10.5, color=INK, gap=3.0, indent=0.0, line=1.45):
        """写一段（自动换行、自动分页）。超长文本按字符切块，确保不会溢出整页。"""
        s = (s or "").strip()
        if not s:
            return
        for i in range(0, len(s), MAX_CHARS):
            self._put(s[i:i + MAX_CHARS], size, color, indent, line)
        self.y += gap

    def _put(self, s, size, color, indent, line):
        def _box():
            return self.fitz.Rect(MARGIN + indent, self.y, A4[0] - MARGIN, BOTTOM)

        rect = _box()
        if rect.height < size * line * 1.2:
            self._new_page()
            rect = _box()
        left = self.page.insert_textbox(rect, s, fontname=self.font, fontsize=size,
                                        color=color, align=0, lineheight=line)
        if left < 0:                  # 当前页放不下 → 换页重排
            self._new_page()
            rect = _box()
            left = self.page.insert_textbox(rect, s, fontname=self.font, fontsize=size,
                                            color=color, align=0, lineheight=line)
            left = max(left, 0.0)
        # insert_textbox 返回「还剩下多少高度」，据此推进光标
        self.y = rect.y0 + (rect.height - max(left, 0.0))

    def tobytes(self):
        if self.font_path:
            # 内嵌的是**完整的**系统字体文件（msyh.ttc ≈ 19MB）。不裁剪的话，
            # 一张三四页的成绩单就有 18MB —— 学生根本发不出去，老师也未必收得下。
            # 子集化只保留页面真正用到的字形：实测 18.76MB → 0.10MB，中文照常可提取。
            # 裁不动也不能让导出失败，所以失败就保持原样（宁可文件大）。
            try:
                self.doc.subset_fonts()
            except Exception:
                pass
        # deflate：tobytes 默认不压缩，不显式打开会白白放大（save 的默认值才是压缩）
        return self.doc.tobytes(deflate=True, garbage=4)


def _head(d):
    d.text("实验报告评阅建议单", size=19, color=INK, gap=2, line=1.25)
    # 这里是 PDF 不是 Markdown，不要写 ** 强调符 —— 它会被原样印出来
    d.text("本单由 AI 依据既定评分标准生成，仅供参考；"
           "教师可逐项调整，最终成绩以教师评定为准。",
           size=9.5, color=ACCENT, gap=2)
    d.rule()


def _basic(d, res, course):
    ri = res.run_info
    lines = [
        f"报告编号：{res.report_id or '（未命名）'}",
        f"评阅模型：{res.model or '（未知）'}"
        + (f"　｜　耗时 {res.elapsed_sec:.1f} 秒" if res.elapsed_sec else ""),
        f"生成时间：{(ri.created_at if ri and ri.created_at else datetime.now().isoformat(timespec='seconds'))}",
    ]
    if course:
        lines.append(f"课程 / 背景：{course}")
    if ri:
        lines.append(f"评分标准版本：{(ri.rubric_source_hash or '')[:12] or '（未记录）'}"
                     f"　｜　报告指纹：{(ri.report_hash or '')[:12] or '（未记录）'}")
    for x in lines:
        d.text(x, size=9.5, color=MUTED, gap=1.5)


def _total(d, res):
    d.space(4)
    d.text(f"总分：{res.total:.1f} / 100", size=16, color=INK, gap=2, line=1.25)
    if abs(float(res.ai_total or 0.0) - float(res.total or 0.0)) >= 0.05:
        d.text(f"（AI 原始分 {res.ai_total:.1f}，其中 {len(res.overrides)} 项经人工调整）",
               size=9, color=MUTED, gap=2)
    if res.total_incomplete:
        # 系统错误 ≠ 学生失分：这一条必须印在单子上，否则会被当成真实得分
        d.text("注意：本次有评分点因系统错误未能判定，已从总分分母中剔除，"
               "未被当作 0 分计入；该部分需人工复核。",
               size=9.5, color=ACCENT, gap=2)


def _judgement_text(d, idx, it, jd, ov):
    # 系统错误这一项**绝不能**显示 AI 判定与分数。pipeline 的 system_error_judgement()
    # 给这类项填的是 verdict="miss"、score=0 —— 照原样印出来就是「AI 判定 0（未达）」，
    # 读者只会理解成「学生没做到」，而这正是整个项目最不能出的错（系统错误 ≠ 学生失分）。
    # 单子上是要打印、要截图的，光靠下面一行小字红字声明救不回来。
    syserr = bool(jd and jd.system_error)
    if syserr:
        final = f"{ov.new_score:g}" if ov else "待人工判定"
        head = f"满分 {it.max_score:g}　｜　系统错误，本次未作出判定　｜　最终 {final}"
    else:
        verdict = _VERDICT.get((jd.verdict if jd else ""), "未判定")
        score = f"{jd.score:g}" if jd else "—"
        conf = f"{jd.confidence:.2f}" if jd and jd.confidence else "—"
        final = f"{ov.new_score:g}" if ov else "（未改）"
        head = (f"满分 {it.max_score:g}　｜　AI 判定 {score}（{verdict}）　｜　"
                f"最终 {final}　｜　置信度 {conf}")
    d.text(f"【{idx}】{it.name}", size=11, color=INK, gap=1.5, line=1.3)
    d.text(head, size=9.5, color=MUTED, gap=1.5, indent=12)
    if it.criteria:
        d.text(f"判定标准：{it.criteria}", size=9.5, color=MUTED, gap=1.5, indent=12)
    if jd and jd.reason:
        d.text(f"判定理由：{jd.reason}", size=10, color=INK, gap=1.5, indent=12)
    for ev in (jd.evidence if jd else []):
        loc = f"（{ev.section_id}）" if ev.section_id else ""
        # 证据是逐字引用：单子上加引号，避免读者以为是我们改写的话
        d.text(f"原文依据{loc}：“{ev.quote}”", size=9.5, color=(0.2, 0.25, 0.45),
               gap=1.5, indent=12)
    if jd and jd.dropped_quotes:
        d.text(f"（{len(jd.dropped_quotes)} 条引用未能在原文中逐字命中，"
               f"已作废该证据，未计入判定）", size=9, color=ACCENT, gap=1.5, indent=12)
    if jd and jd.system_error:
        d.text(f"系统错误：{jd.system_error}。该项不计入总分，需人工判定。",
               size=9, color=ACCENT, gap=1.5, indent=12)
    elif jd and jd.needs_review:
        d.text("该判定不确定，需人工复核。", size=9, color=ACCENT, gap=1.5, indent=12)
    if ov:
        d.text(f"人工改分：{ov.original_score:g} → {ov.new_score:g}"
               + (f"（原因：{ov.reason}）" if ov.reason else ""),
               size=9.5, color=(0.1, 0.4, 0.2), gap=1.5, indent=12)
    d.space(4)


def build_grading_pdf(res, course: str = "") -> bytes:
    """把一次评阅结果渲染成可打印、可分发的成绩单（返回 PDF 字节）。"""
    if res is None:
        raise ValueError("没有可导出的评阅结果")
    d = _Doc()
    _head(d)
    _basic(d, res, course)
    _total(d, res)

    jd_by_item = {j.rubric_item_id: j for j in (res.judgements or [])}
    ov_by_item = {o.rubric_item_id: o for o in (res.overrides or [])}

    d.rule()
    d.text("分项明细", size=12.5, color=INK, gap=3, line=1.3)
    for i, it in enumerate(res.items or [], 1):
        _judgement_text(d, i, it, jd_by_item.get(it.id), ov_by_item.get(it.id))

    fb = res.feedback
    if fb and (fb.summary or fb.suggestions):
        d.rule()
        d.text("整体反馈与改进建议", size=12.5, color=INK, gap=3, line=1.3)
        if fb.summary:
            d.text(fb.summary, size=10, gap=3)
        for k, s in enumerate(fb.suggestions or [], 1):
            d.text(f"{k}. {s}", size=10, gap=2, indent=6)
        if fb.generated_by != "model":
            d.text("（本段由规则兜底生成：模型未返回可用反馈。）",
                   size=9, color=MUTED, gap=2)
        if fb.error:
            d.text(f"（模型反馈生成失败：{fb.error}）", size=9, color=ACCENT, gap=2)

    d.rule()
    d.text("本单的边界", size=11, color=INK, gap=2, line=1.3)
    for x in ["① 分项得分由代码按既定权重加总，模型只提供单点判定。",
              "② 所有判定依据均为报告原文的逐字引用，Python 侧做过精确匹配。",
              "③ 因系统错误未能判定的评分点不计入总分分母，也绝不当作 0 分。",
              "④ 本单不构成成绩认定；如有异议，以教师复核结论为准。"]:
        d.text(x, size=9.5, color=MUTED, gap=1.5)
    return d.tobytes()
