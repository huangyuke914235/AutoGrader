# -*- coding: utf-8 -*-
"""生成「一个班」的实验报告演示样本（教学演示用，非真实学生作业）

    python tools/make_class_roster.py

产出 `samples_class/C01..C09.txt`，刻意覆盖教师最常遇到的几种典型形态：

| 编号 | 形态 | 典型缺陷 |
|---|---|---|
| C01 | 优秀 | 各项完整、有数据与误差讨论 |
| C02 | 优秀 | 完整但分析略浅 |
| C03 | 中等 | 有代码与结果，**缺环境说明** |
| C04 | 中等 | 结果只有标题、**没有数据** |
| C05 | 中等 | 实现写了、**没有分析总结** |
| C06 | 偏弱 | 只有步骤罗列，缺代码与数据 |
| C07 | 偏弱 | 开头没写实验目的，其余零散 |
| C08 | 偏弱 | 几乎只有截图占位说明 |
| C09 | 中等 | 数据齐全但**没有误差/复杂度讨论** |

为什么这些样本要进仓库：班级学情看板（classview）是本项目"教学管理"那一半的核心，
它需要一批**有共性短板**的报告才能演示出价值。只用三份真实样本（S02/S03/S04）
看不出"这个班普遍卡在哪"。这批是自造内容，不含任何真实学生信息，可安全公开。

真实学生作业请放在 `data/samples/`（已 gitignore），不要混进这里。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "samples_class")

HEAD = """深圳大学《Java程序设计》课程实验报告
实验项目：面向对象程序设计（银行账户类）
学院：计算机与软件学院　专业：计算机科学与技术
"""

# 各形态共用的段落片段
P_PURPOSE_OK = """
一、实验目的
本次实验旨在掌握 Java 类的封装、构造方法与方法重载，理解封装对数据合法性的保护作用，
并能够用 main 方法验证类的行为是否符合预期。
"""

P_ENV_OK = """
二、实验环境
JDK 25（Oracle LTS），IntelliJ IDEA 2025.2，Windows 11 23H2。
命令行执行 java -version 与 javac -version，输出一致，环境可用。
"""

P_STEPS_OK = """
三、实验步骤
1. 打开 IDEA，新建 Java 项目 JavaExperiment2，SDK 选择 JDK 25；
2. 在 src 下新建类 BankAccount；
3. 编写构造方法与 deposit / withdraw / toString 方法；
4. 编写 main 方法构造两个账户并依次调用各方法；
5. 运行并观察控制台输出。
"""

P_CODE_OK = """
四、核心实现
public class BankAccount {
    private String accountNo;
    private double balance;

    public BankAccount(String accountNo, double balance) {
        if (balance < 0) { throw new IllegalArgumentException("初始余额不能为负"); }
        this.accountNo = accountNo;
        this.balance = balance;
    }

    public boolean deposit(double amount) {
        if (amount <= 0) { return false; }
        this.balance += amount;
        return true;
    }

    public boolean withdraw(double amount) {
        if (amount <= 0 || amount > this.balance) { return false; }
        this.balance -= amount;
        return true;
    }

    public double getBalance() { return this.balance; }
}
"""

P_RESULT_OK = """
五、运行结果
控制台输出如下：
账户 6222-0001 初始余额：1000.00 元
存入 500.00 元，当前余额：1500.00 元
取款 2000.00 元失败：余额不足
取款 300.00 元成功，当前余额：1200.00 元
共执行 4 次操作，成功 3 次，失败 1 次

| 操作序号 | 操作类型 | 金额 | 结果 | 余额 |
|---|---|---|---|---|
| 1 | 存款 | 500.00 | 成功 | 1500.00 |
| 2 | 取款 | 2000.00 | 失败 | 1500.00 |
| 3 | 取款 | 300.00 | 成功 | 1200.00 |
"""

P_ANALYSIS_OK = """
六、结果分析
1. 取款 2000 元时方法返回 false 且余额保持 1500.00 元不变，
   说明封装后的余额没有被外部直接修改，数据合法性得到了保证；
2. 三次操作全部通过返回值表达结果，调用方可以根据返回值决定后续处理，
   比抛出异常更适合表达"业务上可以预期的失败"；
3. 构造函数中做了参数校验，保证对象一旦创建就是合法状态。
"""

P_SUMMARY_OK = """
七、实验总结
通过本次实验掌握了三点：私有字段配合公有方法可以把数据校校验收敛到类的内部，
避免调用方绕过校验；构造方法内的校验能保证对象初始状态合法；
返回值比异常更适合表达可预期的业务失败。
不足之处：只用 main 方法做了手工验证，没有写 JUnit 测试用例，覆盖场景偏少，
下一步打算补上参数化测试。
"""


def body(parts) -> str:
    return HEAD + "".join(parts)


REPORTS = {
    # ---- 优秀 ----
    "C01": body([P_PURPOSE_OK, P_ENV_OK, P_STEPS_OK, P_CODE_OK, P_RESULT_OK,
                 P_ANALYSIS_OK, P_SUMMARY_OK]),
    "C02": body([P_PURPOSE_OK, P_ENV_OK, P_STEPS_OK, P_CODE_OK, P_RESULT_OK,
                 P_ANALYSIS_OK,
                 "\n八、实验总结\n本次实验完成了银行账户类的设计与验证，掌握了封装的基本用法。\n"]),

    # ---- 中等：缺环境说明 ----
    "C03": body([P_PURPOSE_OK, P_STEPS_OK, P_CODE_OK, P_RESULT_OK, P_ANALYSIS_OK,
                 P_SUMMARY_OK]),

    # ---- 中等：结果只有标题、没有数据 ----
    "C04": body([P_PURPOSE_OK, P_ENV_OK, P_STEPS_OK, P_CODE_OK,
                 "\n五、运行结果\n运行结果如下所示：\n\n（此处为运行截图）\n",
                 "\n六、结果分析\n程序运行正确，符合预期，说明类设计没有问题。\n",
                 "\n七、实验总结\n本次实验收获很大，学会了面向对象的基本写法。\n"]),

    # ---- 中等：没有分析总结 ----
    "C05": body([P_PURPOSE_OK, P_ENV_OK, P_STEPS_OK, P_CODE_OK, P_RESULT_OK]),

    # ---- 偏弱：只有步骤罗列 ----
    "C06": body([P_PURPOSE_OK, P_ENV_OK, P_STEPS_OK,
                 "\n四、运行结果\n按照上述步骤操作，程序可以正常运行。\n",
                 "\n五、实验总结\n本次实验让我熟悉了 IDEA 的基本操作。\n"]),

    # ---- 偏弱：开头没写目的 ----
    "C07": body([P_ENV_OK, P_STEPS_OK,
                 "\n三、核心实现\n编写了 BankAccount 类，实现了存款和取款两个方法。\n",
                 "\n四、实验总结\n程序能跑通，基本完成了实验要求。\n"]),

    # ---- 偏弱：几乎只有截图占位 ----
    "C08": body(["一、实验内容\n按老师要求完成银行账户类的编写。\n",
                 "二、实验步骤\n参照课本第 4 章的例子进行操作（截图见下）。\n",
                 "三、运行结果\n（此处为运行截图）\n（此处为代码截图）\n",
                 "四、实验小结\n已完成本次实验。\n"]),

    # ---- 中等：数据齐全但没有误差/复杂度讨论 ----
    "C09": body([P_PURPOSE_OK, P_ENV_OK, P_STEPS_OK, P_CODE_OK, P_RESULT_OK,
                 "\n六、实验总结\n本次实验完成了所有要求的操作，结果与预期一致。\n"]),
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for rid, text in REPORTS.items():
        # 全文脱敏扫描：这批是自造样本，本就不该含学号/手机/邮箱
        import re
        for label, pat in (("学号", r"\b(?:19|20)\d{8,9}\b"),
                           ("手机", r"\b1[3-9]\d{9}\b"),
                           ("邮箱", r"[\w.+-]+@[\w-]+\.[\w.]+")):
            if re.search(pat, text):
                raise SystemExit(f"{rid} 命中{label}，拒绝写出")
        p = os.path.join(OUT, rid + ".txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
        print(f"  {rid}.txt  {len(text)} 字")
    print(f"\n已生成 {len(REPORTS)} 份班级演示样本 → {os.path.relpath(OUT, ROOT)}/")
    print("下一步：用它跑一次真实批量评阅，生成班级学情看板的演示数据：")
    print("  python tools/build_class_demo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
