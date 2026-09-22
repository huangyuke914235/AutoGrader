# -*- coding: utf-8 -*-
"""生成参赛答辩 PPT（7 页，16:9，浅色 + 深青强调）

用法：
    python tools/make_ppt.py

输出：
    <仓库上级目录 或 当前目录>/AutoGrader-参赛答辩.pptx

纪律：
    这一页 PPT 上的每一句技术承诺，都必须能在仓库代码或演示中指认。
    指认不出的（例如并不存在的 prompt v1→v2 对比文档）一律不写。
    若团队信息是占位符，请自行替换后再提交。
"""
import math
import os

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.oxml import parse_xml

FONT = "微软雅黑"
INK = "1F2937"      # 主文字
MUTED = "6B7280"    # 次要文字
TEAL = "0F766E"     # 主色
AMBER = "B45309"    # 强调（只用在焦点数字上）
BG_SOFT = "F3F4F6"  # 浅底卡片
WHITE = "FFFFFF"
LINE = "D1D5DB"

W, H = 13.333, 7.5
EA_XML = ('<a:ea xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'typeface="%s"/>')

# 对外指标 —— 只改这里，然后重跑本脚本
# BEFORE：缺陷 #3（PDF 断行 / 短引用一票否决）修复**之前**的完整重测值
# AFTER ：缺陷 #3 修复**之后**的复测值，重跑 tools/benchmark.py 后填进来
METRICS_BEFORE = {"mae": "20.44", "acc": "0%", "trace": "84.9%"}
METRICS_AFTER = {"mae": "7.56", "acc": "33.3%", "trace": "93.0%"}


def set_font(run, name=FONT):
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    latin = rPr.find(qn("a:latin"))
    ea = parse_xml(EA_XML % name)
    if latin is not None:
        latin.addnext(ea)
    else:
        rPr.append(ea)


def rect(slide, x, y, w, h, fill=None, line=None, radius=0.08):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                 Inches(x), Inches(y), Inches(w), Inches(h))
    shp.adjustments[0] = radius
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = RGBColor.from_string(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = RGBColor.from_string(line)
        shp.line.width = Pt(1)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    tf = shp.text_frame
    tf.word_wrap = True
    return shp


def txt(slide, x, y, w, h, lines, size=14, color=INK, bold=False,
        align=PP_ALIGN.LEFT, space_after=8, line_spacing=1.25, anchor=MSO_ANCHOR.TOP):
    """lines: 字符串 或 (字符串, {可选覆盖})"""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    for i, item in enumerate(lines):
        text, opt = (item, {}) if isinstance(item, str) else item
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = opt.get("align", align)
        p.space_after = Pt(opt.get("space_after", space_after))
        p.line_spacing = opt.get("line_spacing", line_spacing)
        r = p.add_run()
        r.text = text
        r.font.size = Pt(opt.get("size", size))
        r.font.bold = opt.get("bold", bold)
        r.font.color.rgb = RGBColor.from_string(opt.get("color", color))
        set_font(r)
    return box


def page_base(slide, title, kicker=None, page=1, total=7):
    """统一的标题区 + 页脚条"""
    if kicker:
        txt(slide, 0.62, 0.42, 11.5, 0.3, [(kicker, {"size": 11, "color": TEAL, "bold": True})])
        y_title = 0.74
    else:
        y_title = 0.5
    txt(slide, 0.62, y_title, 11.9, 0.72, [(title, {"size": 27, "bold": True})])
    rect(slide, 0.62, y_title + 0.78, 1.05, 0.055, fill=TEAL, radius=0.5)
    # 页脚
    txt(slide, 0.62, 6.92, 8.0, 0.28,
        [("AutoGrader · RAG-E 四阶判定链 · 每条判定都可溯源到报告原文",
          {"size": 10, "color": MUTED})])
    txt(slide, 11.2, 6.92, 1.5, 0.28,
        [(f"{page:02d} / {total:02d}", {"size": 10, "color": MUTED, "align": PP_ALIGN.RIGHT})])


FOOTER_TOP = 6.85


def est_height(lines, width, size=12.5, space_after=6):
    """估算一个文本框需要的高度（英寸），用来让卡片自适应、避免文字溢出底色块"""
    total = 0.0
    for item in lines:
        t, opt = (item, {}) if isinstance(item, str) else item
        sz = opt.get("size", size)
        cpl = max(1, int((width * 72) / sz))
        n = math.ceil(len(t) / cpl)
        total += n * sz * 1.30 / 72 + opt.get("space_after", space_after) / 72
    return total


def card(slide, x, y, w, h, head, body, head_color=TEAL, fill=BG_SOFT, size=12.5):
    lines = body if isinstance(body, list) else [body]
    filled = [(t, {"size": size, "color": INK, "space_after": 6}) if isinstance(t, str) else t
              for t in lines]
    need = est_height(filled, w - 0.5, size)
    h_used = max(h, 0.54 + need + 0.18)
    if y + h_used > FOOTER_TOP:
        print(f"  ⚠ 卡片触底：y={y} 高度={h_used:.2f} 底部={y + h_used:.2f} | {head}")
    rect(slide, x, y, w, h_used, fill=fill)
    rect(slide, x, y, 0.055, h_used, fill=head_color, radius=0.5)
    txt(slide, x + 0.28, y + 0.18, w - 0.5, 0.34,
        [(head, {"size": 15, "bold": True, "color": head_color if head_color != TEAL else INK})])
    txt(slide, x + 0.28, y + 0.54, w - 0.5, max(0.2, h_used - 0.72), filled)
    return h_used


def bignum(slide, x, y, w, value, label, note=""):
    rect(slide, x, y, w, 1.62, fill=BG_SOFT)
    txt(slide, x + 0.24, y + 0.16, w - 0.48, 0.72,
        [(value, {"size": 34, "bold": True, "color": AMBER})])
    txt(slide, x + 0.24, y + 0.88, w - 0.48, 0.34,
        [(label, {"size": 13, "bold": True})])
    if note:
        txt(slide, x + 0.24, y + 1.2, w - 0.48, 0.32,
            [(note, {"size": 10.5, "color": MUTED})])


def main():
    prs = Presentation()
    prs.slide_width = Inches(W)
    prs.slide_height = Inches(H)
    blank = prs.slide_layouts[6]

    # ---------- 1 封面 ----------
    s = prs.slides.add_slide(blank)
    rect(s, 0, 0, W, H, fill=WHITE)
    rect(s, 0, 0, 0.22, H, fill=TEAL, radius=0)
    txt(s, 0.95, 1.7, 11.4, 1.42,
        [("AutoGrader", {"size": 52, "bold": True, "color": INK}),
         ("实验报告智能评阅平台", {"size": 22, "color": TEAL, "space_after": 0})])
    txt(s, 0.95, 3.28, 11.4, 0.9,
        [("不做「让 AI 打个分」，而做「把评分标准变成", {"size": 17, "color": INK}),
         ("可核查、可溯源、可校准的判定流水线」。", {"size": 17, "color": INK})])
    chips = ["RAG-E 四阶判定链", "证据原文逐字校验", "人在回路",
             "总分由代码加总"]
    x = 0.95
    for c in chips:
        wch = 0.45 + len(c) * 0.165
        rect(s, x, 4.15, wch, 0.5, fill=BG_SOFT)
        txt(s, x, 4.15, wch, 0.5, [(c, {"size": 11.5, "color": TEAL, "bold": True})],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        x += wch + 0.22
    txt(s, 0.95, 5.5, 11.4, 0.9,
        [("粤港澳大湾区 AI Coding 创新大赛 · 方向一：AI + 教学管理助手",
          {"size": 13, "color": MUTED}),
         ("团队成员：＿＿＿（专业 / 分工）　·　开源协议：MIT", {"size": 13, "color": MUTED})])

    # ---------- 2 赛题与痛点 ----------
    s = prs.slides.add_slide(blank)
    page_base(s, "为什么要做：批改的痛点是「不一致」，不只是「慢」", "01 · 赛题方向与痛点", 2)
    card(s, 0.62, 1.75, 3.9, 2.0, "助教的时间被重复劳动吃掉",
         ["一门课 60 人 × 每份 8~10 分钟 ≈ 10 小时",
          "大量时间花在「找证据」而不是「判断」"])
    card(s, 4.72, 1.75, 3.9, 2.0, "学生的反馈只有分数",
         ["只知道扣了多少，不知道扣在哪一句",
          "改进建议往往是「分析不够深入」这类空话"])
    card(s, 8.82, 1.75, 3.9, 2.0, "直接让 AI 打总分不可接受",
         ["黑箱：说不出为什么给这个分",
          "不可核查：引用可能根本不在原文里"])
    rect(s, 0.62, 4.05, 12.1, 0.9, fill=BG_SOFT)
    txt(s, 0.9, 4.05, 11.6, 0.9,
        [("我们要的不是「更快的分数」，而是「每个分数都能被指认出依据」——"
          "证据在第几节、原文是哪一句、判定是谁做的。",
          {"size": 15, "bold": True})], anchor=MSO_ANCHOR.MIDDLE)
    card(s, 0.62, 5.2, 12.1, 1.4, "我们自己实测到的「不一致」",
         ["同一份报告连跑 10 次，有 2 次判定在 hit / partial 之间漂移——"
          "这正是 G 一致性守卫存在的原因。",
          "（数据来自仓库 stability_log.json，界面「批量测试 → 同一份重复评阅」可复现）"],
         head_color=AMBER, size=11.5)

    # ---------- 3 方法论 ----------
    s = prs.slides.add_slide(blank)
    page_base(s, "RAG-E 四阶判定链：把评分拆成可核查的四步", "02 · 作品简介与方法论", 3)
    steps = [
        ("R", "评分点原子化", "把教师用自然语言写的评分标准，拆成 8~12 个彼此不重叠、可独立判定的原子评分点。"),
        ("A", "证据锚定判定", "逐个评分点独立判定，必须回引报告原文；引用逐字匹配不上即作废重跑。"),
        ("G", "一致性守卫", "低置信或两次判定不一致 → 显式标记「待人工复核」并置顶。"),
        ("E", "反馈生成", "基于已定稿的判定生成评语与可执行建议，不参与打分。"),
    ]
    x = 0.62
    for letter, name, desc in steps:
        rect(s, x, 1.72, 2.9, 2.15, fill=WHITE, line=LINE)
        txt(s, x + 0.24, 1.9, 0.7, 0.6, [(letter, {"size": 26, "bold": True, "color": TEAL})])
        txt(s, x + 0.9, 2.02, 1.9, 0.4, [(name, {"size": 15, "bold": True})])
        txt(s, x + 0.24, 2.55, 2.42, 1.2, [(desc, {"size": 12, "space_after": 0})])
        x += 3.1
    rect(s, 0.62, 4.15, 12.1, 0.06, fill=LINE, radius=0.5)
    txt(s, 0.62, 4.35, 12.1, 0.34, [("三条不可违背的工程铁律", {"size": 16, "bold": True})])
    rules = [
        ("分数由代码加总", "模型只输出单点判定 hit / partial / miss，模型输出里没有 total 字段。"),
        ("无证据的判断不算数", "每条引用必须能原文逐字匹配；失败即作废重跑，绝不放宽规则让数字变好看。"),
        ("不确定就交给人", "低置信或双跑不一致 → 标记待复核；教师可在界面直接改分并留痕。"),
    ]
    x = 0.62
    for head, desc in rules:
        rect(s, x, 4.82, 3.9, 1.9, fill=BG_SOFT)
        txt(s, x + 0.24, 5.0, 3.4, 0.34, [(head, {"size": 14, "bold": True, "color": TEAL})])
        txt(s, x + 0.24, 5.42, 3.42, 1.2, [(desc, {"size": 12, "space_after": 0})])
        x += 4.03
    txt(s, 0.62, 6.72, 12.1, 0.3,
        [("模型唯一被允许输出的，是「单个评分点 + 判定 + 理由 + 原文引用」——其余全由确定性代码决定。",
          {"size": 11.5, "color": MUTED})])

    # ---------- 4 AI 能力说明 ----------
    s = prs.slides.add_slide(blank)
    page_base(s, "AI 被允许做什么，不被允许做什么", "03 · AI 能力边界（这是作品的核心）", 4)
    rect(s, 0.62, 1.72, 5.9, 0.52, fill=TEAL)
    txt(s, 0.62, 1.72, 5.9, 0.52, [("✓ AI 被允许做的", {"size": 15, "bold": True, "color": WHITE})],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    rect(s, 6.82, 1.72, 5.9, 0.52, fill=AMBER)
    txt(s, 6.82, 1.72, 5.9, 0.52, [("✗ AI 不被允许做的", {"size": 15, "bold": True, "color": WHITE})],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    allow = [
        "把自然语言评分标准拆成原子评分点（R）",
        "对单个评分点作出 hit / partial / miss 判定（A）",
        "以「另一位助教」身份做一次独立复核（G）",
        "把已定稿的判定改写成学生读得懂的反馈（E）",
    ]
    deny = [
        "算总分 —— 总分由代码加总，模型拿不到这个权力",
        "给出无法在原文逐字匹配的引用 —— 直接作废重跑",
        "决定最终成绩 —— 低置信/不一致转人工，教师可改分留痕",
        "接触未脱敏的原始作业 —— 脱敏 + 证据窗口裁剪后才入库",
    ]
    for i, t in enumerate(allow):
        txt(s, 0.85, 2.42 + i * 0.72, 5.5, 0.6, [(t, {"size": 13.5, "space_after": 0})])
    for i, t in enumerate(deny):
        txt(s, 7.05, 2.42 + i * 0.72, 5.5, 0.6, [(t, {"size": 13.5, "space_after": 0})])
    rect(s, 0.62, 5.5, 12.1, 1.25, fill=BG_SOFT)
    txt(s, 0.9, 5.5, 11.6, 1.25,
        [("一句话概括分工：AI 负责「判断」，代码负责「算数与把关」，人负责「拍板」。",
          {"size": 15, "bold": True}),
         ("把这三件事分开，才有可能在评委问「你这个 87 分是怎么来的」时，指认出每一个字的出处。",
          {"size": 12.5, "color": MUTED})], anchor=MSO_ANCHOR.MIDDLE)

    # ---------- 5 技术方案 ----------
    s = prs.slides.add_slide(blank)
    page_base(s, "技术方案：6 个文件、两条部署链路、一份隐私边界", "04 · 技术方案与工程实现", 5)
    files = [
        ("app.py", "Streamlit 界面：评阅 / 详情对照 / 评分点 / 导出 / 批量测试"),
        ("models.py", "Pydantic 数据契约（ItemJudgement 里没有 total 字段）"),
        ("prompts.py", "RAG-E 四阶段 prompt 模板，由人维护"),
        ("llm.py", "模型调用 + JSON 宽容解析 + 防幻觉闸门（最值钱的文件）"),
        ("parser.py", "报告解析与章节切分：纯确定性代码，不调模型"),
        ("pipeline.py", "四阶编排；总分在这里由代码加总"),
    ]
    y = 1.68
    for name, desc in files:
        rect(s, 0.62, y, 7.4, 0.52, fill=BG_SOFT)
        txt(s, 0.82, y, 1.5, 0.52, [(name, {"size": 12.5, "bold": True, "color": TEAL})],
            anchor=MSO_ANCHOR.MIDDLE)
        txt(s, 2.35, y, 5.5, 0.52, [(desc, {"size": 12})], anchor=MSO_ANCHOR.MIDDLE)
        y += 0.62
    card(s, 8.3, 1.68, 4.42, 1.8, "部署：两条链路",
         ["Streamlit Cloud：可交互 Demo",
          "GitHub Pages：主页 + 离线可看案例",
          "（评委打不开 Demo 时的保底）",
          "密钥走 st.secrets，不进仓库"], size=12)
    card(s, 8.3, 3.75, 4.42, 1.95, "隐私：原始作业不出本地",
         ["data/ 已写入 .gitignore，仓库里没有原始报告",
          "公开案例双重处理：全文脱敏 + 正文裁剪为证据窗口",
          "学号 / 姓名 / 班级 / 电话 / 邮箱已置零"], size=11.5)
    card(s, 8.3, 5.78, 4.42, 0.9, "分界线清晰",
         ["解析 / 切分 / 加总 / 校验：确定性代码"],
         head_color=AMBER, size=11.5)

    # ---------- 6 自评测 ----------
    s = prs.slides.add_slide(blank)
    page_base(s, "自评测：敢自测，也敢把不好看的数字写出来", "05 · 真实数据与自评测结果", 6)
    bignum(s, 0.62, 1.72, 3.9, METRICS_BEFORE["mae"], "平均绝对误差 MAE（分）",
           "系统总分与人工总分之差的绝对值平均")
    bignum(s, 4.72, 1.72, 3.9, METRICS_BEFORE["acc"], "误差 ≤5 分的报告占比", "9 份参评报告")
    bignum(s, 8.82, 1.72, 3.9, METRICS_BEFORE["trace"], "证据可溯源率",
           "每条引用回原文精确匹配，绝不放宽规则")
    after_txt = ("缺陷修复后复测：MAE {mae} 分 / 误差≤5 占比 {acc} / 可溯源率 {trace}　——"
                 "该修复发生在 gold 解封之后，是靠「系统性低估」这个信号查出来的，"
                 "我们无法自证它没有沾到 gold 的光".format(**METRICS_AFTER)
                 if METRICS_AFTER.get("mae") else
                 "缺陷修复后复测：待重跑 tools/benchmark.py 后填入（修复发生在 gold 解封之后，"
                 "将如实标注修复动机）")
    rect(s, 0.62, 3.42, 12.1, 0.62, fill=BG_SOFT)
    txt(s, 0.9, 3.42, 11.6, 0.62, [(after_txt, {"size": 11.5, "bold": True, "color": AMBER})],
        anchor=MSO_ANCHOR.MIDDLE)
    rect(s, 0.62, 4.14, 12.1, 0.95, fill=BG_SOFT)
    txt(s, 0.9, 4.14, 11.6, 0.95,
        [("评测方法：10 份真实实验报告由非主程成员独立人工打分（gold set）——"
          "打分期间不看系统结果、不与主程讨论，封存到评测当天才解封。",
          {"size": 13, "space_after": 4}),
         ("这是纪律问题：让写 prompt 的人来定义「正确答案」，等于对着答案改答案。",
          {"size": 11.5, "color": MUTED})], anchor=MSO_ANCHOR.MIDDLE)
    card(s, 0.62, 5.19, 12.1, 1.5, "诚实记录：第一次跑出来是 MAE 31.67 分",
         ["9 份里 8 份被系统性低估。查下去发现判定阶段只把报告 top-4 章节、每章截断 2500 字"
          "送进模型，模型实际只看到全文的 7%~11%——纯工程缺陷，与 prompt 无关。",
          "修好后再完整重跑是 20.44 分：同一套代码两次跑分差这么多，是真实方差，我们不藏。"
          "三篇排查记录都在仓库 docs/ 下。"],
         head_color=AMBER, size=11.5)

    # ---------- 7 AI 协作过程 + 团队 + 展望 ----------
    s = prs.slides.add_slide(blank)
    page_base(s, "AI 协作过程、团队与展望", "06 · AI 工具使用与下一步", 7)
    card(s, 0.62, 1.72, 6.0, 2.3, "AI 帮我们抓到的两个真实缺陷",
         ["① 上下文丢失：模型只看到报告 7%~11% 内容（MAE 31.67 → 13.33）",
          "② 反馈生成失败：异常被 except 吞掉，查不到原因；最后靠另一项功能测试"
          "才炸出真凶——输出的 JSON 含未转义引号",
          "两篇排查记录都在仓库 docs/ 下，是当时写下的，不是事后美化"],
         head_color=AMBER, size=12)
    card(s, 6.82, 1.72, 5.9, 2.3, "AI 在工程里的位置",
         ["代码骨架、prompt 初稿、界面实现由 AI 协作产出",
          "判定规则、评分点定义、gold set 打分由人完成",
          "「不许吞异常」「不许放宽匹配规则」是我们给自己立的规矩"], size=12)
    card(s, 0.62, 4.18, 6.0, 1.6, "团队与分工",
         ["成员 A：主程 —— 代码 / 部署 / 版本管理",
          "成员 B：产品与材料 —— 脱敏 / 评分点 / 试用 / PPT / 视频",
          "（此处按实际姓名与专业替换）"], size=11.5)
    card(s, 6.82, 4.18, 5.9, 1.6, "下一步",
         ["把评分模板扩到更多课程与学科",
          "增加班级维度的批量视图与学情统计",
          "把「重复评阅」做成常态化的稳定性看板"], size=11.5)
    txt(s, 0.62, 5.92, 12.1, 0.9,
        [("本页每一句技术承诺，都能在仓库代码或在线演示中指认；指认不出的我们已经删掉了。"
          "所有数字均可由 tools/batch_run.py + tools/benchmark.py 复现。",
          {"size": 12.5, "color": MUTED, "space_after": 4}),
         ("提交前提示：MAE / 误差占比 / 可溯源率以最新一次 benchmark 为准，"
          "若与本次不一致请以新数字更新本页。", {"size": 12.5, "color": AMBER, "bold": True})])

    out_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(out_dir, "AutoGrader-参赛答辩.pptx")
    prs.save(out)
    print("已生成：", out)
    return out


if __name__ == "__main__":
    main()
