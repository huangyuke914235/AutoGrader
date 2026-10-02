# -*- coding: utf-8 -*-
"""生成一份「带截图的实验报告」测试 PDF，用于验证多模态读图链路

为什么要专门造一份：现有样本（samples_demo/*.txt）是纯文本，走不到读图分支。
要验证 OCR 真的能把截图里的内容读出来，必须有一份**图片里才有信息**的 PDF。

刻意这样设计：
- 正文里有代码（触发 caption 档：只描述图片，不重复转录正文）
- 「运行结果」只以**截图**形式存在，正文里不写输出内容
  → 如果 OCR 有效，就能读出截图里的具体数值；如果无效，这块证据就是空的
- 截图上有明确的、可校验的具体数字，便于人工核对读得对不对

输出到 tools/_ocr_test.pdf（下划线开头，已 gitignore，不会被提交）。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont
import pymupdf as fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tools", "_ocr_test.pdf")
IMG = os.path.join(ROOT, "tools", "_ocr_test_shot.png")

# 截图里要出现的**可校验事实**：OCR 如果读对了，这些数字必须被转录出来
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


def _font(size):
    """找一个能显示中文的字体，找不到就退回默认（会显示方框，但流程不中断）"""
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
        p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_console_png() -> str:
    """画一个像样的控制台截图（深底浅字），内容就是上面那几行"""
    w, h = 900, 60 + len(CONSOLE_LINES) * 34 + 20
    img = Image.new("RGB", (w, h), (12, 12, 12))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 34], fill=(38, 38, 38))
    d.text((14, 9), "Windows PowerShell", font=_font(16), fill=(220, 220, 220))
    f = _font(19)
    y = 50
    for line in CONSOLE_LINES:
        color = (240, 240, 240)
        if "失败" in line:
            color = (255, 120, 120)
        elif "成功" in line:
            color = (140, 230, 140)
        d.text((14, y), line, font=f, fill=color)
        y += 34
    img.save(IMG)
    return IMG


BODY_P1 = """深圳大学《Java程序设计》课程实验报告

实验项目：面向对象程序设计（银行账户类）
实验时间：2026年10月

一、实验目的
掌握 Java 类的封装、构造方法与方法重载，理解封装对数据合法性的保护作用，
能够用单元测试的方式验证类的行为是否符合预期。

二、实验环境
JDK 25（Oracle LTS），IntelliJ IDEA 2025.2，Windows 11。
命令行验证：java -version 与 javac -version 版本一致。

三、核心实现
public class BankAccount {
    private String accountNo;
    private double balance;

    public BankAccount(String accountNo, double balance) {
        this.accountNo = accountNo;
        this.balance = balance;
    }

    public void deposit(double amount) {
        if (amount <= 0) {
            throw new IllegalArgumentException("存款金额必须为正数");
        }
        this.balance += amount;
    }

    public boolean withdraw(double amount) {
        if (amount > this.balance) {
            return false;
        }
        this.balance -= amount;
        return true;
    }
}

四、运行结果
运行结果见下方的控制台截图（本次测试共执行 4 次操作）。
"""

BODY_P2 = """五、结果分析
从截图可以看出，取款金额超过余额时方法返回 false 且余额保持不变，
说明封装后的余额没有被外部直接修改，数据合法性得到了保证。

六、实验总结
通过本次实验掌握了以下三点：
1. 私有字段 + 公有方法可以把数据校验收敛到类的内部，避免调用方绕过校验；
2. 构造方法里做参数校验，可以保证对象创建出来就是合法状态；
3. 返回值（boolean）比抛异常更适合表达"业务上可以预期的失败"。

本次实验的不足之处：没有给 BankAccount 写 JUnit 测试用例，
仅用 main 方法做了手工验证，覆盖的场景偏少。
"""


def main():
    shot = make_console_png()
    doc = fitz.open()

    p1 = doc.new_page(width=595, height=842)
    p1.insert_textbox(fitz.Rect(56, 56, 539, 700), BODY_P1,
                      fontname="china-s", fontsize=10.5, lineheight=1.5)
    # 把「控制台截图」贴在正文下方 —— 关键证据只存在于这张图里
    p1.insert_image(fitz.Rect(56, 560, 539, 560 + 300), filename=shot)

    p2 = doc.new_page(width=595, height=842)
    p2.insert_textbox(fitz.Rect(56, 56, 539, 786), BODY_P2,
                      fontname="china-s", fontsize=10.5, lineheight=1.5)

    doc.save(OUT)
    doc.close()

    text = "".join(p.get_text() for p in fitz.open(OUT))
    print(f"已生成：{os.path.relpath(OUT, ROOT)}")
    print(f"  页数 2，文本层 {len(text)} 字")
    # 自检：确保「运行结果」的具体数值**只**在图片里，正文里没有
    leaked = [ln for ln in CONSOLE_LINES if ln.split("：")[-1][:6] in text]
    print(f"  正文中的控制台数值泄漏：{leaked if leaked else '无（符合设计）'}")
    print("  截图内容（正文里读不到，只能靠 OCR）：")
    for ln in CONSOLE_LINES[:3]:
        print("    " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
