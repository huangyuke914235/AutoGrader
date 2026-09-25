# -*- coding: utf-8 -*-
"""学生自检模块测试（不调用模型）

守住这几条，是因为它们一旦破了，模块就从「教学工具」变成「代写工具」或「乱扣分」：
- 权重合计必须是 100（否则总分没有意义）
- 改进方向必须被截断（否则就是帮学生写正文）
- AI 不可用必须降级为「未检测」且不计入总分（系统错误不能算到学生头上）
"""
import pytest

import selfcheck as SC
from models import SelfCheckItem, SelfCheckResult

GOOD_REPORT = """实验目的
用单摆周期公式测量本地重力加速度 g，并与标准值 9.794 m/s² 比较。
原理阐述
小角度摆动时 T = 2π sqrt(L/g)，变形得 g = 4π² L / T²。
实验步骤
1. 用毫米刻度尺测量摆长 L = 0.800 m，重复 3 次取平均
2. 摆角控制在 5° 以内，用电子秒表记录 30 次全振动时间 t = 53.70 s
3. 依次取 L = 0.800、0.900、1.000 m 各测 3 次
数据记录
表 1 原始数据记录
L/m  t/s  T/s
0.800  53.70  1.790
0.900  57.00  1.900
1.000  60.10  2.003
数据处理
由 g = 4π² L / T² 算得 g = 9.84 m/s²，合成不确定度 u = 0.02 m/s²。
误差分析
系统误差：摆角未严格控制在 5° 以内，引入约 0.1% 偏差。
随机误差：秒表启停反应时间约 0.1 s，分摊后影响小于 0.4%。
结论
测得 g = 9.84 ± 0.02 m/s²，与标准值偏差约 0.5%。
参考文献
[1] 大学物理实验教程, 2020.
[2] 不确定度评定方法, JJF 1059-2012.
[3] 单摆实验摆角修正讨论, 2019.
"""

POOR_REPORT = """实验目的
测 g。
实验步骤
1. 测摆长
2. 测时间
数据记录
80.0  53.7
90.0  57.0
结论
g 大概是 9.8。
"""


def test_weights_sum_to_100():
    assert sum(w for w, _e in SC.CHECK_ITEMS.values()) == 100


def test_rule_engine_covers_eight_items():
    items = SC.rule_check(GOOD_REPORT, [])
    assert [it.name for it in items] == SC.RULE_ITEMS
    assert all(it.engine == "rule" for it in items)


def test_good_report_scores_higher_than_poor():
    good = SC.compute_total(SC.rule_check(GOOD_REPORT, []))
    poor = SC.compute_total(SC.rule_check(POOR_REPORT, []))
    assert good > poor + 20, f"好报告 {good} 应显著高于差报告 {poor}"


def test_poor_report_detects_missing_units_and_references():
    items = {it.name: it for it in SC.rule_check(POOR_REPORT, [])}
    assert items["单位与量纲"].status == "偏弱"
    assert items["引用与格式"].status == "缺失"


def test_empty_text_is_not_treated_as_student_failure():
    """空文本是解析失败，不是学生交白卷：状态必须是缺失且写明原因，不能静默给 0 分了事"""
    items = SC.rule_check("", [])
    assert all(it.status == "缺失" for it in items)
    assert any("OCR" in iss.problem for it in items for iss in it.issues)


CS_REPORT = """一 实验目的
掌握 Java 泛型与集合框架的使用，能够独立实现带类型约束的容器类。
二 实验步骤
1. 安装 JDK 25，配置 JAVA_HOME 到 C:\\Program Files\\Java\\jdk-25
2. 用 javac -version 验证编译器版本
3. 编写 Main.java 并运行，记录控制台输出
三 运行结果
图 1 控制台输出截图
程序输出：sum=1234，耗时 12ms，准确率 96.5%
四 实验结论
泛型在编译期保证类型安全，避免了运行期 ClassCastException。
"""


def test_line_rules_need_raw_text_not_canonical():
    """canonical() 把换行压成空格，行级规则会全废 —— 这条守住「必须传原文」这件事。

    实测：同一份报告，用 canonical 文本跑编号步骤只有 65 分（只认出第一个「一 」），
    用保留换行的原文是 100 分。差别就是「完整报告被判偏弱」和「判对」。
    """
    import parser as P
    canonical = P.canonical(CS_REPORT)
    sections = P.split_sections(CS_REPORT)
    by_raw = {i.name: i.score for i in SC.rule_check(
        canonical, sections, "计算机类实验", raw_text=CS_REPORT)}
    by_canon = {i.name: i.score for i in SC.rule_check(
        canonical, sections, "计算机类实验", raw_text=canonical)}
    assert by_raw["步骤可复现性"] > by_canon["步骤可复现性"]


def test_profile_keywords_change_section_recognition():
    """同一份计算机类报告用理工科词表跑，章节识别应明显变差 —— 说明词表确实在起作用"""
    import parser as P
    cs = SC.rule_check(P.canonical(CS_REPORT), P.split_sections(CS_REPORT),
                       "计算机类实验", raw_text=CS_REPORT)
    sci = SC.rule_check(P.canonical(CS_REPORT), P.split_sections(CS_REPORT),
                        "理工科实验", raw_text=CS_REPORT)
    assert cs[0].score > sci[0].score


def test_figure_reference_must_have_number():
    """「截图说明」是给学生的要求，不是已有图表；不带编号不能算图表"""
    text = "五 截图说明\n需包含：JDK 下载页面截图、安装完成界面截图。\n"
    items = {i.name: i for i in SC.rule_check(text, [], "计算机类实验", raw_text=text)}
    assert items["图表规范"].score == 0
    assert items["图表规范"].status == "缺失"


def test_empty_result_section_is_reported_as_empty():
    """「运行结果」只有标题没正文 —— 要说「正文为空」，不能把后一节内容算成它的"""
    text = "三 运行结果\n四 实验结论\n完成了。\n"
    items = {i.name: i for i in SC.rule_check(text, [], "计算机类实验", raw_text=text)}
    assert "正文为空" in items["原始数据表"].issues[0].problem


def test_section_text_does_not_count_its_own_title_as_body():
    sec = type("S", (), {"title": "运行结果", "text": "运行结果 只有标题没有内容"})
    items = {i.name: i for i in SC.rule_check("x", [sec], "计算机类实验", raw_text="x")}
    assert items["原始数据表"].score < 100


# 界面上「实验类型」下拉框的可选项（自定义除外）。直接引用模块里的唯一真源，
# 这样界面改选项时这条测试会自动跟着改，不会两边写串。
UI_OPTIONS = [o for o in SC.UI_OPTIONS if o != "自定义…"]

BIO_REPORT = """一 实验目的
观察洋葱根尖细胞有丝分裂各时期的染色体形态。
二 实验原理
分生区细胞分裂旺盛，经解离、漂洗、染色后制片可在显微镜下观察。
三 材料与方法
洋葱、显微镜、载玻片、醋酸洋红染液；解离 5 min，漂洗 3 min，染色 3 min。
四 实验结果
视野中可见间期、前期、中期、后期细胞，中期染色体排列在赤道板上。
五 结果分析
统计 200 个细胞中各时期占比，中期约占 12%。
六 讨论
解离时间过长会导致细胞破碎，影响染色体形态观察。
七 实验结论
成功观察到有丝分裂各时期，中期染色体形态清晰。
参考文献
[1] 遗传学实验教程，2020.
"""


def test_each_ui_option_maps_to_its_own_profile():
    """界面给了 5 个学科，就必须有 5 套**真的不同**的词表。

    曾经只有 2 套词表，于是物理 / 通用理工科 / 化学 / 生物四个选项跑出完全一样的分数
    —— 界面承诺了引擎没有的能力。这条测试防止再退化回去。
    """
    profiles = [SC.pick_profile(o) for o in UI_OPTIONS]
    assert len(set(profiles)) == len(UI_OPTIONS), f"选项与词表没有一一对应：{profiles}"
    assert set(profiles) <= set(SC.PROFILE_KEYWORDS), "有选项落到了不存在的词表上"
    # 反向：也不许有「没人能选到」的词表，那等于写了死代码
    assert set(profiles) == set(SC.PROFILE_KEYWORDS), \
        f"有词表没有任何选项能选中：{set(SC.PROFILE_KEYWORDS) - set(profiles)}"


def test_discipline_specific_headings_recognized_only_by_matching_profile():
    """「试剂与仪器」只有化学词表认得，「材料与方法」只有生物词表认得。

    这才叫学科自适应：不是换个名字显示，而是判据真的不一样。
    """
    chem = "三 试剂与仪器\n0.1000 mol/L NaOH 标准溶液、酚酞指示剂、酸式滴定管。\n"
    bio = "三 材料与方法\n洋葱、显微镜、载玻片、醋酸洋红染液。\n"
    for frag, owner in ((chem, "基础化学实验"), (bio, "生物实验")):
        assert "步骤可复现性" in SC.locate_sections([], frag, owner)
        for other in SC.PROFILE_KEYWORDS:
            if other == owner:
                continue
            assert "步骤可复现性" not in SC.locate_sections([], frag, other), \
                f"「{owner}」专属标题不该被「{other}」词表认出"


def test_biology_report_scores_higher_under_biology_profile():
    bio = SC.compute_total(SC.rule_check(BIO_REPORT, [], "生物实验", raw_text=BIO_REPORT))
    generic = SC.compute_total(SC.rule_check(BIO_REPORT, [], "理工科实验",
                                             raw_text=BIO_REPORT))
    assert bio > generic, f"生物词表 {bio} 应高于通用理工科 {generic}"


def test_direction_is_truncated_to_40_chars():
    long_text = "这是一条非常长的改进方向说明" * 10
    issue = SC._issue(problem="x", direction=long_text)
    assert len(issue.direction) <= SC.MAX_DIRECTION_LEN


def test_total_excludes_undetected_items():
    """AI 挂了不能当成学生 0 分：未检测项必须从分母里剔除"""
    items = [
        SelfCheckItem(name="实验目的", score=100, status="通过", engine="rule"),
        SelfCheckItem(name="误差分析", score=0, status="未检测", engine="ai"),
    ]
    assert SC.compute_total(items) == 100.0


def test_ai_failure_degrades_to_undetected(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("模型不可用")

    monkeypatch.setattr(SC, "call_json", boom)
    items = SC.ai_check(GOOD_REPORT, [], "大学物理")
    assert [it.name for it in items] == SC.AI_ITEMS
    assert all(it.status == "未检测" for it in items)
    assert all(it.issues and "暂不可用" in it.issues[0].problem for it in items)


def test_ai_output_is_sanitized(monkeypatch):
    """模型给了超长 direction / 非法 status / 越界分数，代码层必须全部收口"""
    from models import SelfCheckIssue as SI, SelfCheckPayload

    payload = SelfCheckPayload(items=[
        SelfCheckItem(name="原理阐述", score=150, status="随便写", engine="ai",
                      issues=[SI(problem="推导跳步", location="第 2 节",
                                 impact="较大", direction="补上推导" * 30)]),
        SelfCheckItem(name="不存在的项", score=50, status="偏弱", engine="ai"),
    ])

    def fake(*a, **k):
        return payload

    monkeypatch.setattr(SC, "call_json", fake)
    items = SC.ai_check(GOOD_REPORT, [], "大学物理")
    assert [it.name for it in items] == SC.AI_ITEMS          # 越权项被丢弃
    first = items[0]
    assert first.score == 100                                 # 越界分数被夹住
    assert first.status == "偏弱"                              # 非法状态被兜底
    assert len(first.issues[0].direction) <= SC.MAX_DIRECTION_LEN


def test_diff_marks_fixed_item():
    old = SelfCheckResult(items=[SelfCheckItem(name="误差分析", score=40, status="缺失")])
    new = SelfCheckResult(items=[SelfCheckItem(name="误差分析", score=90, status="通过")])
    rows = SC.diff_results(old, new)
    assert rows[0]["状态"] == "已修复"
    assert rows[0]["变化"] == "+50"


def test_markdown_contains_disclaimer():
    res = SC.run_selfcheck(GOOD_REPORT, [], experiment_type="大学物理",
                           use_ai=False, report_id="S99")
    md = SC.to_markdown(res)
    assert "与教师正式评分无关" in md
    assert "S99" in md


def test_run_selfcheck_without_ai_returns_only_rule_items():
    res = SC.run_selfcheck(GOOD_REPORT, [], use_ai=False)
    assert len(res.items) == len(SC.RULE_ITEMS)
    assert res.undetected == []
    assert 0 <= res.total <= 100
