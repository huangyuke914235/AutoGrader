# -*- coding: utf-8 -*-
"""可打印成绩单（PDF）—— 这里要钉的是「印出来的东西不能被误读」

成绩单是会离开这个页面的：被打印、转发、截图。所以三件事必须有测试兜底：
1. 中文真的能提取出来（字体没落对的话，会是满纸方框或空白，肉眼一眼看不出原因）；
2. 「仅供参考、以教师为准」必须出现在纸上 —— 一张看起来像正式成绩的 PDF
   会造成实打实的误导，这是我们这个项目最不能出的错；
3. 系统错误未判定的评分点必须印出来，且不能被当成 0 分。
"""
import pytest

import report_pdf
from models import (Evidence, Feedback, GradingResult, HumanOverride,
                    ItemJudgement, RubricItem)

MARKER_REASON = "本例理由独有标记QWERTY"


def _res(total=72.5, ai_total=None, incomplete=False, with_feedback=True):
    items = [RubricItem(id="r1", name="实验目的", criteria="说明目的", max_score=30),
             RubricItem(id="r2", name="误差分析", criteria="给出误差来源", max_score=70)]
    jds = [
        ItemJudgement(rubric_item_id="r1", verdict="hit", score=30, confidence=0.9,
                      reason=MARKER_REASON,
                      evidence=[Evidence(section_id="2 实验目的", quote="本实验旨在测定光栅常数")]),
        ItemJudgement(rubric_item_id="r2", verdict="partial", score=42.5, confidence=0.5,
                      reason="误差来源描述不完整", needs_review=True,
                      dropped_quotes=["这句并不在原文里"]),
    ]
    return GradingResult(
        report_id="RPT-1", items=items, judgements=jds, total=total,
        ai_total=ai_total if ai_total is not None else total,
        total_incomplete=incomplete, model="deepseek-chat", elapsed_sec=12.3,
        overrides=[HumanOverride(rubric_item_id="r2", original_score=42.5,
                                 new_score=35, reason="教师下调")],
        feedback=Feedback(summary="整体结构完整", suggestions=["补上误差的量化估计"],
                          generated_by="model") if with_feedback else None)


def _text(pdf_bytes):
    """提取全文文字。

    注意里面的 `\\xa0` → 空格归一化：**内嵌系统字体时**，PyMuPDF 会把空格写成
    U+00A0(NBSP)，提取出来就是 `\\xa0`（内置的 china-s/helv/tiro 则不会）。
    渲染层两者等价 —— 已渲染成图肉眼确认过，只是提取文本的表现形式。
    不归一化的话，断言会因为一个空格符而假失败，掩盖真正的问题。
    真正要守护的「西文别被排成全角」由下面的宽度断言负责。
    """
    import parser as P
    fitz = P.import_fitz()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(p.get_text() for p in doc).replace("\xa0", " ")


def test_pdf_is_valid_and_chinese_is_extractable():
    """中文必须能提取出来 —— 字体没落对时页面会是空白/方框，且不会报错。"""
    blob = report_pdf.build_grading_pdf(_res())
    assert blob[:5] == b"%PDF-"
    text = _text(blob)
    assert "实验报告评阅建议单" in text
    assert "实验目的" in text and "误差分析" in text
    assert MARKER_REASON in text, "中文正文没有真正写进去"


def test_score_sheet_stays_small_enough_to_send():
    """成绩单得真能发出去。内嵌的是**完整的**系统字体文件（msyh.ttc ≈ 19MB），
    不裁剪时一份三四页的单子实测 18.76MB —— 这个体积放进下载按钮是荒唐的。

    门槛故意设得很松（2MB），只在有人删掉 subset_fonts() 时才会变红。
    注：系统没有中文字体时走内置兜底路径，本来就小，这条会平凡通过 ——
    不算假绿灯，兜底路径本来就不存在这个问题。
    """
    blob = report_pdf.build_grading_pdf(_res())
    mb = len(blob) / 1048576
    assert mb < 2, f"成绩单 {mb:.1f}MB，太大了：字体大概没有做子集化"


def test_disclaimer_is_on_the_paper():
    """一份看起来像正式成绩的 PDF 会造成实际的误导，这句话必须在纸上。"""
    text = _text(report_pdf.build_grading_pdf(_res()))
    assert "仅供参考" in text
    assert "最终成绩" in text and "教师" in text


def test_human_override_is_printed():
    text = _text(report_pdf.build_grading_pdf(_res()))
    assert "人工改分" in text and "35" in text and "教师下调" in text


def test_ai_original_total_shows_up_only_when_it_differs():
    same = _text(report_pdf.build_grading_pdf(_res(total=72.5, ai_total=72.5)))
    assert "AI 原始分" not in same
    diff = _text(report_pdf.build_grading_pdf(_res(total=65, ai_total=72.5)))
    assert "AI 原始分" in diff


def test_undetected_items_are_flagged_not_scored_zero():
    """系统错误 ≠ 学生失分：单子上必须写清「不计入总分」，而不是留个 0。"""
    text = _text(report_pdf.build_grading_pdf(_res(incomplete=True)))
    assert "未能判定" in text and "分母" in text


def test_system_error_item_never_shows_a_score():
    """系统错误这一项在单子上**绝不能**出现「0（未达）」。

    pipeline 的 system_error_judgement() 给这类项填的就是 verdict="miss"、score=0，
    照原样印出来读者只会理解成「学生没做到」—— 而系统错误不是学生失分。
    这份单子会被打印、被截图，光靠下面一行红字声明是救不回来的。
    """
    from pipeline import system_error_judgement
    items = [RubricItem(id="r1", name="实验目的", criteria="说明目的", max_score=40),
             RubricItem(id="r2", name="误差分析", criteria="给出误差来源", max_score=60)]
    jds = [ItemJudgement(rubric_item_id="r1", verdict="hit", score=40, confidence=0.9,
                         reason="写得清楚"),
           system_error_judgement(items[1], "调用超时")]
    res = GradingResult(report_id="SE-1", items=items, judgements=jds, total=100,
                        ai_total=100, total_incomplete=True, model="deepseek-chat")
    text = _text(report_pdf.build_grading_pdf(res))
    assert "系统错误，本次未作出判定" in text, "没写清这一项根本没被判定"
    assert "待人工判定" in text, "系统错误项的「最终」栏不该是「（未改）」"
    assert "0（未达）" not in text, "系统错误被印成了「0 分 · 未达」，等于算成学生失分"
    assert "该项不计入总分" in text, "没写明不计入总分"

    # 反向对照：摘掉 system_error 标记，同一份数据立刻就会被印成「0（未达）」。
    # 说明上面那条断言不是空的 —— 它确实在拦这个错误，而不是碰巧成立。
    naked = [jds[0], ItemJudgement(rubric_item_id="r2", verdict="miss", score=0.0,
                                   confidence=0.0, reason="学生未做误差分析")]
    res2 = GradingResult(report_id="SE-2", items=items, judgements=naked, total=40,
                         ai_total=40, model="deepseek-chat")
    assert "0（未达）" in _text(report_pdf.build_grading_pdf(res2))


def test_evidence_and_dropped_quotes_are_both_shown():
    """证据是逐字引用；被丢弃的引用也要留痕，否则「为什么这项扣分」说不清。"""
    text = _text(report_pdf.build_grading_pdf(_res()))
    assert "本实验旨在测定光栅常数" in text
    assert "逐字" in text or "未能在原文中逐字命中" in text


def test_long_report_does_not_crash_and_paginates():
    """几十个评分点的长篇报告要能自动分页，而不是把文字挤出页面。"""
    items, jds = [], []
    for i in range(60):
        items.append(RubricItem(id=f"r{i}", name=f"长项{i}", criteria="标准",
                                max_score=10))
        jds.append(ItemJudgement(rubric_item_id=f"r{i}", verdict="hit", score=10,
                                 confidence=0.8, reason="理由" * 40,
                                 evidence=[Evidence(section_id="1", quote="原文" * 60)]))
    res = GradingResult(report_id="LONG", items=items, judgements=jds, total=80,
                        model="deepseek-chat")
    blob = report_pdf.build_grading_pdf(res)
    import parser as P
    doc = P.import_fitz().open(stream=blob, filetype="pdf")
    assert doc.page_count >= 3, "长报告必须自动分页"
    text = "\n".join(p.get_text() for p in doc)
    assert "长项59" in text, "最后一页的内容不能丢"
    assert text.count("仅供参考") >= doc.page_count - 1, "页脚免责声明应逐页出现"


def test_exporting_nothing_is_an_error_not_an_empty_sheet():
    """没有结果时宁可报错，也不要生成一张空白的「成绩单」被当成真成绩发出去。"""
    with pytest.raises(ValueError):
        report_pdf.build_grading_pdf(None)


def test_latin_is_not_typeset_full_width():
    """内置 CJK 字体会把**西文也当全角**排：实测 china-s 下 12 字符占到 130pt，
    即 ≈1.08em/字 —— 「deepseek-chat」会被印成「d e e p s e e k - c h a t」。

    这张单子是要打印出来交到老师手上的，模型名这样排很糟，所以有系统字体时必须内嵌。
    没有系统字体时（比如云端容器）显式跳过，而不是假装这条通过。
    """
    if not report_pdf._pick_font():
        pytest.skip("本机/容器没有可用系统 CJK 字体，只能退回内置字体（西文偏宽）")
    import parser as P
    doc = P.import_fitz().open(stream=report_pdf.build_grading_pdf(_res()),
                               filetype="pdf")
    hits = doc[0].search_for("deepseek-chat")
    assert hits, "模型名应当印在首页的「评阅模型」一行上"
    per = (hits[0].x1 - hits[0].x0) / len("deepseek-chat")
    size = 9.5                       # _basic() 里这一行的字号
    assert per < 0.8 * size, f"西文被排成了全角：{per:.2f}pt/字符（字号 {size}pt）"


def test_broken_system_font_falls_back_to_builtin(tmp_path, monkeypatch):
    """系统字体是「优先」而非「必须」：字体文件坏掉时要退回内置，而不是印出一纸空白。

    为什么必须守这条：云端容器不一定有中文字体。内嵌失败如果没兜住，
    部署过去就是满纸方框 —— 而且**不会报错**，上线时才发现。
    等价的失败还有「内嵌了一个不含中文字形的字体」，那种连异常都不抛，
    由 test_pdf_is_valid_and_chinese_is_extractable 兜底。
    """
    bad = tmp_path / "broken.ttf"
    bad.write_bytes(b"not a real font file")          # 实测 insert_font 会抛 FzErrorLibrary
    monkeypatch.setattr(report_pdf, "_FONT_CANDIDATES", [str(bad)])
    text = _text(report_pdf.build_grading_pdf(_res()))
    assert "实验报告评阅建议单" in text
    assert MARKER_REASON in text, "退回内置字体后中文正文同样要真的写进去"
