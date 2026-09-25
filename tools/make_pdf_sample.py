# -*- coding: utf-8 -*-
"""生成一份「可打印成绩单」样例（PDF + 首页 PNG），用来人工核对排版。

跑法：python tools/make_pdf_sample.py

为什么要有这个：成绩单是排版很密的产物，改动 `report_pdf.py` 之后
光看单测通过是不够的（分页、西文宽度、字体子集化都是肉眼才看得出的东西）。
样例里刻意覆盖了全部状态 —— 命中 / 部分命中 / 未达 / 系统错误未判定 /
人工改分 / 证据被作废 —— 一页就能看出哪种状态没印对。

数据是**合成的**，不含任何真实学生报告。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import report_pdf  # noqa: E402
from parser import import_fitz  # noqa: E402
from pipeline import system_error_judgement  # noqa: E402
from models import (Evidence, Feedback, GradingResult, HumanOverride,  # noqa: E402
                    ItemJudgement, RubricItem)

OUT_PDF = os.path.join(ROOT, "docs", "样例-可打印成绩单.pdf")
OUT_PNG = os.path.join(ROOT, "docs", "样例-可打印成绩单-首页.png")


def sample() -> GradingResult:
    """一份覆盖全部展示状态的合成评阅结果。"""
    items = [
        RubricItem(id="r1", name="实验目的", criteria="开头明确陈述实验目的，且与实验内容直接相关",
                   max_score=15),
        RubricItem(id="r2", name="实验步骤完整", criteria="步骤按顺序完整列出，涵盖准备到结束的主要环节",
                   max_score=20),
        RubricItem(id="r3", name="数据处理与误差分析",
                   criteria="给出测量数据的处理过程与误差来源的定量估计", max_score=25),
        RubricItem(id="r4", name="结果讨论", criteria="结论被数据支撑，并对异常现象作出解释",
                   max_score=25),
        RubricItem(id="r5", name="格式规范", criteria="图表有编号与标题，单位与有效数字统一",
                   max_score=15),
    ]
    jds = [
        ItemJudgement(rubric_item_id="r1", verdict="hit", score=15, confidence=0.95,
                      reason="报告开头单列「实验目的」一节，明确写出测定光栅常数及其在"
                             "分光计调节中的应用，与后续测量步骤一一对应。",
                      evidence=[Evidence(section_id="1 实验目的",
                                         quote="本实验旨在用分光计测定光栅常数，并掌握分光计的调节与使用方法。")]),
        ItemJudgement(rubric_item_id="r2", verdict="hit", score=18, confidence=0.88,
                      reason="步骤按操作顺序完整列出，涵盖仪器调节、读数与记录，关键环节无缺失。",
                      evidence=[Evidence(section_id="2 实验步骤",
                                         quote="先调节望远镜聚焦于无穷远，再调节载物台使光栅面与准直管垂直。")]),
        ItemJudgement(rubric_item_id="r3", verdict="partial", score=13, confidence=0.52,
                      reason="给出了逐次测量的原始数据与平均值，但只把误差归结为「仪器不准」，"
                             "没有对读数误差作任何定量估计，也未提及误差传递。",
                      needs_review=True,
                      evidence=[Evidence(section_id="3 数据记录",
                                         quote="五组读数分别为 129°23′、129°24′、129°23′、129°25′、129°24′。")],
                      dropped_quotes=["本实验的主要误差来自读数视差"]),
        ItemJudgement(rubric_item_id="r4", verdict="miss", score=5, confidence=0.71,
                      reason="结论只复述了测得的波长数值，未与标准值比较，也未解释偏差来源。",
                      evidence=[Evidence(section_id="5 结论", quote="测得钠黄光波长为 589.6 nm。")]),
        # 第 5 项刻意用**生产代码里的那个构造函数**来造，而不是手写一个"好看"的版本：
        # 手写容易写出 verdict="" 这种现实里不会出现的组合，样例就成了自欺。
        system_error_judgement(items[4], "模型返回的 JSON 中 score 字段缺失，已按系统错误处理"),
    ]
    # 分值口径（这是本项目最容易搞错的一处，样例必须自洽）：
    #   已检测权重 = 15+20+25+25 = 85（r5 因系统错误从分母剔除，绝不当作 0 分）
    #   最终得分   = 15+18+10(人工改分后)+5 = 48   → 48 / 85 × 100 = 56.5
    #   AI 原始分  = 15+18+13+5            = 51   → 51 / 85 × 100 = 60.0
    return GradingResult(
        report_id="DEMO-2026-0007",
        items=items, judgements=jds,
        total=56.5,
        ai_total=60.0,
        total_incomplete=True,  # r5 未判定 → 已从总分分母剔除
        overrides=[HumanOverride(rubric_item_id="r3", original_score=13, new_score=10,
                                 reason="误差分析确有缺失，扣分从严")],
        model="deepseek-chat", elapsed_sec=37.5,
        feedback=Feedback(
            summary="整体结构完整，操作步骤记录规范，这部分是本次报告的强项。"
                    "主要短板在数据处理与误差分析：有原始数据但缺少误差的定量讨论，"
                    "结论也没有与标准值对照。",
            suggestions=["补上误差来源的定量估计（至少给出读数误差对结果的影响量级）。",
                         "把结论中的测得值与标准值作比较，并计算相对误差。",
                         "统一数据表与结论中的有效数字位数。"],
            generated_by="model"))


def main():
    res = sample()
    blob = report_pdf.build_grading_pdf(res, course="大学物理实验（分光计测光栅常数）")
    os.makedirs(os.path.dirname(OUT_PDF), exist_ok=True)
    with open(OUT_PDF, "wb") as f:
        f.write(blob)
    fitz = import_fitz()
    doc = fitz.open(stream=blob, filetype="pdf")
    doc[0].get_pixmap(dpi=140).save(OUT_PNG)
    print(f"PDF : {OUT_PDF}  （{len(blob)/1024:.0f} KB，{doc.page_count} 页）")
    print(f"首页: {OUT_PNG}")


if __name__ == "__main__":
    main()
