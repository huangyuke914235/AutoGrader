# -*- coding: utf-8 -*-
"""生成「多模态 OCR 前后对比」：同一份截图型报告，读图前 vs 读图后

    python tools/build_ocr_comparison.py

为什么值得单独做一个对比：班级学情队列里有一份报告（157 字、几乎只有截图占位）
被判 0 分 —— 这正是"看不到就判没有"的典型。用**同一份报告**跑两次
（关掉读图 / 开启读图），把两次的分数与证据并列出来，比任何描述都更能说明
OCR 到底解决了什么问题。

前提：报告必须是**图片型**的（内容在截图里，文本层很薄）。
`samples_class/C08.txt` 是文字占位，所以这里另外生成一份**带截图页的 PDF**，
内容与 C08 对应（"此处为运行截图"真的做成截图）。

输出 `data/class_demo/ocr_comparison.json` + 控制台对照表。
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont
import pymupdf as fitz

import ocr as O
import parser as P
from pipeline import default_rubric, run_grading, run_ocr_enrichment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "class_demo")
OUT = os.path.join(OUT_DIR, "ocr_comparison.json")
PDF = os.path.join(OUT_DIR, "C08_截图版.pdf")

# 截图里要出现的内容：正文只写"（此处为运行截图）"，真正的结果只在这张图里
CONSOLE = [
    "PS D:\\JavaExperiment2\\src> javac BankAccount.java",
    "PS D:\\JavaExperiment2\\src> java BankAccount",
    "账户 6222-0001 初始余额：1000.00 元",
    "存入 500.00 元，当前余额：1500.00 元",
    "取款 2000.00 元失败：余额不足，当前余额 1500.00 元",
    "取款 300.00 元成功，当前余额：1200.00 元",
    "共执行 4 次操作，成功 3 次，失败 1 次",
]
#: 只在截图里出现的关键值（用于自动核对读图效果）
SCREENSHOT_ONLY = ["1500.00", "1200.00", "余额不足", "成功 3 次"]

BODY = """深圳大学《Java程序设计》课程实验报告
实验项目：面向对象程序设计（银行账户类）

一、实验内容
按老师要求完成银行账户类的编写，实现存款、取款与余额查询。

二、实验步骤
参照课本第 4 章的例子进行操作，具体过程见下方截图。

三、运行结果
（此处为运行截图）

四、实验小结
已完成本次实验。
"""


def _font(size):
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc"):
        p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_pdf() -> str:
    """造一份「内容都在截图里」的报告：正文只有占位说明"""
    os.makedirs(OUT_DIR, exist_ok=True)
    w, h = 660, 40 + len(CONSOLE) * 26
    img = Image.new("RGB", (w, h), (12, 12, 12))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 24], fill=(38, 38, 38))
    d.text((10, 5), "Windows PowerShell", font=_font(12), fill=(220, 220, 220))
    f = _font(15)
    y = 34
    for line in CONSOLE:
        color = (240, 240, 240)
        if "失败" in line:
            color = (255, 120, 120)
        elif "成功" in line:
            color = (140, 230, 140)
        d.text((10, y), line, font=f, fill=color)
        y += 26
    png = os.path.join(OUT_DIR, "_c08_shot.png")
    img.save(png, optimize=True)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(fitz.Rect(56, 56, 539, 330), BODY,
                        fontname="china-s", fontsize=10.5, lineheight=1.5)
    page.insert_image(fitz.Rect(76, 350, 519, 350 + 250), filename=png)
    doc.save(PDF, deflate=True, garbage=3)
    doc.close()
    os.remove(png)

    text = "".join(p.get_text() for p in fitz.open(PDF))
    leaked = [k for k in SCREENSHOT_ONLY if k in text]
    if leaked:
        raise SystemExit(f"关键值泄漏到文本层：{leaked} —— 对比就没有意义了")
    print(f"已生成 {os.path.relpath(PDF, ROOT)}（文本层 {len(text)} 字，"
          f"关键值只在截图里）")
    return PDF


def main():
    make_pdf()
    rub = default_rubric()

    # ---- 第一次：不读图（模型只能看到那几行占位说明）----
    full_text, sections = P.parse_file(PDF)
    print(f"\n[1/2] 不读图：正文 {len(full_text)} 字，开始评阅…", flush=True)
    before = run_grading(full_text, sections, "", report_id="C08",
                         rubric=rub, enable_recheck=False)

    # ---- 第二次：开启读图 ----
    print("[2/2] 开启多模态读图后重跑同一份报告…", flush=True)
    enriched, new_sections, ocr_info = run_ocr_enrichment(
        PDF, full_text, sections, enabled=True)
    if not ocr_info.get("used"):
        print(f"OCR 未产出内容：{ocr_info.get('skipped_reason')}")
        return 1
    after = run_grading(enriched, new_sections, "", report_id="C08",
                        rubric=rub, enable_recheck=False)

    def ev_count(res):
        return sum(len(j.evidence) for j in res.judgements)

    print("\n" + "=" * 64)
    print(f"{'':14s}{'不读图':>12s}{'读图后':>12s}")
    print(f"{'总分':14s}{before.total:>12.1f}{after.total:>12.1f}")
    print(f"{'证据条数':14s}{ev_count(before):>12d}{ev_count(after):>12d}")
    print(f"{'正文长度':14s}{len(full_text):>12d}{len(enriched):>12d}")
    print(f"{'OCR 页数':14s}{0:>12d}{ocr_info['pages']:>12d}")
    print("-" * 64)
    print(f"{'评分点':14s}{'前':>12s}{'后':>12s}")
    for it in rub.items:
        b = next((j for j in before.judgements if j.rubric_item_id == it.id), None)
        a = next((j for j in after.judgements if j.rubric_item_id == it.id), None)
        print(f"{it.name:14s}{(b.score if b else 0):>10.1f}/{it.max_score:<1.0f}"
              f"{(a.score if a else 0):>10.1f}/{it.max_score:<1.0f}")
    print("=" * 64)

    # 截图里的关键值是否真的进了证据链
    blob = "\n".join(e.quote for j in after.judgements for e in j.evidence)
    hits = [k for k in SCREENSHOT_ONLY if k in blob or k in enriched]
    print(f"\n截图独有信息进入正文/证据：{len(hits)}/{len(SCREENSHOT_ONLY)} -> {hits}")

    payload = {
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "report": os.path.relpath(PDF, ROOT),
        "note": "同一份截图型报告，读图前后的完整对比（真实模型评阅）",
        "ocr": ocr_info,
        "before": {"total": before.total, "chars": len(full_text),
                   "evidence": ev_count(before), "model": before.model},
        "after": {"total": after.total, "chars": len(enriched),
                  "evidence": ev_count(after), "model": after.model},
        "screenshot_only_found": hits,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
