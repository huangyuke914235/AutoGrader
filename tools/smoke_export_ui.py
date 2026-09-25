# -*- coding: utf-8 -*-
"""导出页（tab4）的界面冒烟：成绩单下载按钮真的挂上去了，而且坏了也不会拖垮同页的 CSV / JSON。

跑法：python tools/smoke_export_ui.py

为什么用 AppTest 而不是浏览器：导出页的问题基本是「渲染期异常」与「状态缺失」两类，
AppTest 在进程内跑真实 Streamlit 运行时，能捕到 `st.exception`，
比截图比对快得多也稳得多（专门的浏览器脚本留给需要点交互的场景，如 verify_history.py）。

不发任何真实模型调用：评阅结果直接注入 session_state。
"""
import os
import sys

os.environ["DEMO_MODE"] = "true"
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/v1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402
import app as APP  # noqa: E402
import report_pdf  # noqa: E402
from models import GradingResult, ItemJudgement, RubricItem  # noqa: E402

PDF_LABEL = "下载 PDF 成绩单（可直接打印）"
FAILED = []


def check(ok, msg):
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        FAILED.append(msg)
    return ok


def fake_result(rid="SMOKE-1"):
    return GradingResult(
        report_id=rid,
        items=[RubricItem(id="r1", name="实验目的", criteria="说明目的", max_score=60),
               RubricItem(id="r2", name="误差分析", criteria="给出误差来源", max_score=40)],
        judgements=[ItemJudgement(rubric_item_id="r1", verdict="hit", score=55,
                                  confidence=0.9, reason="目的清楚"),
                    ItemJudgement(rubric_item_id="r2", verdict="partial", score=30,
                                  confidence=0.5, reason="偏弱", needs_review=True)],
        total=85, ai_total=85, model="deepseek-chat", elapsed_sec=3.2)


def new_app():
    return AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()


def labels(at):
    return [b.label for b in at.download_button]


def main():
    print("① 没有评阅结果时：导出页应给出提示，而不是渲染一个下不了的空附件")
    at = new_app()
    check(not at.exception, f"页面无异常（{len(at.exception)} 个）")
    check(PDF_LABEL not in labels(at), "此时不应出现 PDF 成绩单按钮")
    check(any("暂无结果可导出" in m.value for m in at.info), "给出了「暂无结果可导出」的提示")

    print("② 有评阅结果时：三个下载入口都要在，且 PDF 是真 PDF")
    at = new_app()
    at.session_state["result"] = fake_result()
    at.run()
    check(not at.exception, f"页面无异常（{len(at.exception)} 个）")
    ls = labels(at)
    check("下载 CSV" in ls and "下载 JSON" in ls and PDF_LABEL in ls,
          f"CSV / JSON / PDF 三个入口都在：{ls}")
    pdfs = [b for b in at.download_button if b.label == PDF_LABEL]
    if pdfs:
        check(str(pdfs[0].url).endswith(".pdf"), f"按钮指向的是 pdf 附件（{pdfs[0].url}）")
    try:
        blob = APP._grading_pdf_cached(fake_result().model_dump_json(), "冒烟课程")
        check(blob[:5] == b"%PDF-", "按钮背后的函数产出的是合法 PDF")
        check(len(blob) < 2 * 1024 * 1024,
              f"体积可发送：{len(blob)/1024:.0f} KB")
    except Exception as e:  # noqa: BLE001
        check(False, f"生成成绩单抛异常：{type(e).__name__}: {e}")

    print("③ 只还原了结果、没留下原文（回看历史时的情形）：详情页应降级而不是整页打崩")
    at = new_app()
    at.session_state["result"] = fake_result("SMOKE-NOFULLTEXT")
    at.run()                      # 故意不写 full_text
    check(not at.exception, f"页面无异常（{len(at.exception)} 个）")
    check(any("没有留存报告原文" in c.value for c in at.caption),
          "给出了「没有留存报告原文」的说明，右侧逐项判定照常显示")

    print("④ 反向对照：成绩单生成失败时，同页的 CSV / JSON 必须还能下")
    real = report_pdf.build_grading_pdf
    report_pdf.build_grading_pdf = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("故意炸掉，验证降级"))
    try:
        # 用全新的 report_id，避开 _grading_pdf_cached 的内容缓存
        at = new_app()
        at.session_state["result"] = fake_result("SMOKE-BOOM")
        at.run()
        check(not at.exception, "生成失败没有让页面崩（异常被接住了）")
        check(any("PDF 成绩单暂时生成不了" in w.value for w in at.warning),
              "向用户说明了失败原因与出路")
        ls = labels(at)
        check("下载 CSV" in ls and "下载 JSON" in ls, "CSV / JSON 不受影响，仍可下载")
    finally:
        report_pdf.build_grading_pdf = real

    print()
    print("SMOKE OK" if not FAILED else f"SMOKE FAILED（{len(FAILED)} 项）")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
