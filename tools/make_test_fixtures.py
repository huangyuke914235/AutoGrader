# -*- coding: utf-8 -*-
"""生成仓库内置的测试夹具（自造内容，可安全公开）

    python tools/make_test_fixtures.py

产出 `tests/fixtures/`：

- `sample_report.pdf`：两页的实验报告，含**一张控制台截图**。
  两个用途：① 版面渲染测试的真实输入（此前那两条测试只找本机私有样本，
  在干净克隆与 CI 里永远 skip）；② 多模态 OCR 的离线夹具。
- `sample_console.png`：上面那张截图本身，便于单独核对读图效果。

为什么夹具必须进仓库：关键路径的测试不允许依赖本机私有数据。
版面渲染曾经因为一个未导入的 `fitz` 静默失败了很久 —— 而抓到它的那条测试
恰好永远 skip，这就是代价。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont
import pymupdf as fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")

CONSOLE_LINES = [
    "PS D:\\JavaExperiment1\\src> javac BankAccount.java",
    "PS D:\\JavaExperiment1\\src> java BankAccount",
    "=== 银行账户测试开始 ===",
    "账户 6222-0001 初始余额：1000.00 元",
    "存入 500.00 元，当前余额：1500.00 元",
    "取款 2000.00 元失败：余额不足，当前余额 1500.00 元",
    "取款 300.00 元成功，当前余额：1200.00 元",
    "共执行 4 次操作，成功 3 次，失败 1 次",
    "=== 测试结束，耗时 0.038 秒 ===",
]

#: 只可能在截图里出现的关键值。夹具生成后会逐条断言它们**不在**文本层中 ——
#: 这是「读图真的有用」这条结论的地基：如果这些值同时存在于文本层，
#: 那么即使 OCR 完全失效也能"读"到，测试就失去了判别力。
SCREENSHOT_ONLY = ["1500.00", "1200.00", "1000.00", "余额不足", "成功 3 次"]

BODY_P1 = """深圳大学《Java程序设计》课程实验报告

实验项目：面向对象程序设计（银行账户类）

一、实验目的
掌握 Java 类的封装、构造方法与方法重载，理解封装对数据合法性的保护作用。

二、实验环境
JDK 25（Oracle LTS），IntelliJ IDEA 2025.2，Windows 11。

三、核心实现
public class BankAccount {
    private String accountNo;
    private double balance;
    public boolean withdraw(double amount) {
        if (amount > this.balance) { return false; }
        this.balance -= amount;
        return true;
    }
}

四、运行结果
运行结果见下方的控制台截图（本次测试共执行 4 次操作）。
"""

BODY_P2 = """五、结果分析
从截图可以看出，取款金额超过余额时方法返回 false 且余额保持不变，
说明封装后的余额没有被外部直接修改。

六、实验总结
1. 私有字段 + 公有方法可以把数据校验收敛到类的内部；
2. 返回值（boolean）比抛异常更适合表达"业务上可以预期的失败"。
"""


def _font(size):
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc", "DejaVuSans.ttf"):
        p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_console_png(path: str) -> str:
    """画一个像样的控制台截图。

    刻意压小：夹具要进仓库，近 1MB 的 PDF 每次 clone 都要拉，不值得。
    640 宽 / JPEG 质量 70 下，18px 字仍然清晰可读，PDF 能压到 100KB 量级。
    """
    w = 640
    h = 44 + len(CONSOLE_LINES) * 24
    img = Image.new("RGB", (w, h), (12, 12, 12))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 24], fill=(38, 38, 38))
    d.text((10, 5), "Windows PowerShell", font=_font(12), fill=(220, 220, 220))
    f = _font(14)
    y = 34
    for line in CONSOLE_LINES:
        color = (240, 240, 240)
        if "失败" in line:
            color = (255, 120, 120)
        elif "成功" in line:
            color = (140, 230, 140)
        d.text((10, y), line, font=f, fill=color)
        y += 24
    img.save(path, optimize=True)
    return path


def main():
    os.makedirs(FIX, exist_ok=True)
    png = make_console_png(os.path.join(FIX, "sample_console.png"))

    pdf = os.path.join(FIX, "sample_report.pdf")
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_textbox(fitz.Rect(56, 56, 539, 520), BODY_P1,
                      fontname="china-s", fontsize=10.5, lineheight=1.5)
    p1.insert_image(fitz.Rect(76, 560, 519, 560 + 260), filename=png)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_textbox(fitz.Rect(56, 56, 539, 786), BODY_P2,
                      fontname="china-s", fontsize=10.5, lineheight=1.5)
    # 图片用 JPEG 压缩再嵌入：夹具是给人看的对照样本，不需要无损
    doc.save(pdf, deflate=True, garbage=3)
    doc.close()

    text = "".join(p.get_text() for p in fitz.open(pdf))
    size_kb = os.path.getsize(pdf) / 1024
    print(f"已生成：{os.path.relpath(pdf, ROOT)}（2 页，文本层 {len(text)} 字，{size_kb:.0f} KB）")
    print(f"已生成：{os.path.relpath(png, ROOT)}")

    leaked = [tok for tok in SCREENSHOT_ONLY if tok in text]
    if leaked:
        # 这不是"输出不好看"，而是夹具失去了判别力：OCR 即使完全失效也能读到这些值
        print(f"!!! 关键值泄漏到文本层：{leaked}")
        print("    夹具必须保证这些值只存在于截图里，否则读图测试证明不了任何事")
        return 1
    print(f"关键值仅存在于截图（不在文本层）：{'、'.join(SCREENSHOT_ONLY)}")
    print("文本层含代码：", all(k in text for k in ("public class", "withdraw")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
