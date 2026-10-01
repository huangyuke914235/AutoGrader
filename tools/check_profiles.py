# -*- coding: utf-8 -*-
"""学科词表体检工具（原临时诊断 _diag_profile.py，转正为常驻工具）

用法：
    python tools/check_profiles.py

回答三个问题：
1. 界面上每个学科选项，是否各自落到**不同的**词表？（选项与词表必须 1:1）
2. 换学科后，同一份报告的总分是否真的会变？
3. 学科专属小标题（如化学的「试剂与仪器」、生物的「材料与方法」）
   是否只被自己那张词表认出来，而不被别人抢走？

任何一项不成立，就说明「选了等于没选」，是 bug。
回归测试 tests/test_selfcheck.py 已锁住第 1、3 条，本工具用于人工复核与现场排查。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import selfcheck as SC  # noqa: E402

CHEM = """一 实验目的
掌握酸碱中和滴定的原理与操作，测定未知浓度盐酸的浓度。
二 实验原理
HCl + NaOH = NaCl + H2O，化学计量点时 n(HCl) = n(NaOH)。
三 试剂与仪器
0.1000 mol/L NaOH 标准溶液、酚酞指示剂、酸式滴定管、锥形瓶。
四 实验步骤
1. 用移液管准确移取 25.00 mL 待测盐酸于锥形瓶中
2. 滴加 2 滴酚酞，滴定至微红色且 30 s 不褪色
3. 重复滴定 3 次，取消耗体积相近的数据
五 数据记录
表 1 滴定数据记录
25.00  24.85  0.0985
25.00  24.90  0.0987
六 数据处理
平均消耗 24.88 mL，算得 c(HCl) = 0.0986 mol/L，相对平均偏差 0.1%。
七 误差分析
滴定管读数误差 ±0.02 mL；终点判断偏早引入随机误差。
八 实验结论
测得盐酸浓度 0.0986 mol/L，与标准值偏差 0.2%。
参考文献
[1] 分析化学实验，高等教育出版社，2021.
"""

BIO = """一 实验目的
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


def main() -> int:
    fail = 0

    print("=== 1. 界面选项 -> 词表映射 ===")
    # 「自定义…」本来就该落到默认词表，不参与唯一性检查
    opts = [o for o in SC.UI_OPTIONS if o != "自定义…"]
    for opt in SC.UI_OPTIONS:
        prof = SC.pick_profile(opt)
        ok = prof in SC.PROFILE_KEYWORDS
        print(f"   {opt:12s} -> {prof:12s} {'OK' if ok else '词表缺失!'}")
        if not ok:
            fail += 1
    profiles = [SC.pick_profile(o) for o in opts]
    if len(set(profiles)) != len(profiles):
        print("   !! 存在多个选项落到同一张词表 —— 选了等于没选")
        fail += 1
    if set(profiles) != set(SC.PROFILE_KEYWORDS):
        print(f"   !! 有词表没有任何选项能选中：{set(SC.PROFILE_KEYWORDS) - set(profiles)}")
        fail += 1

    print("\n=== 2. 同一份报告换学科，总分是否真的变 ===")
    for label, text in (("化学报告", CHEM), ("生物报告", BIO)):
        print(f"   --- {label} ---")
        totals = {}
        for prof in SC.PROFILE_KEYWORDS:
            items = SC.rule_check(text, [], prof, raw_text=text)
            total = SC.compute_total(items)
            loc = SC.locate_sections([], text, prof)
            totals[prof] = round(total, 1)
            print(f"      {prof:12s} 总分 {total:6.1f}  结构项 {items[0].score:5.1f}  "
                  f"识别 {len(loc)}/8 {'、'.join(loc)}")
        if len(set(totals.values())) < 2:
            print("      !! 所有学科分数完全相同")
            fail += 1
        else:
            # 记录哪些学科打出了相同的分。注意：这不是 bug ——
            # 一份全用「实验目的/实验原理/实验步骤/数据记录…」这类**通用标题**写成的报告，
            # 各学科词表的公共交集就能全部命中，分数自然一致。
            # 学科词表的价值体现在**不按通用标题写**的报告上（见生物报告的差异）。
            groups = {}
            for p, t in totals.items():
                groups.setdefault(t, []).append(p)
            same = [g for g in groups.values() if len(g) > 1]
            if same:
                print(f"      注：以下学科得分相同（该报告用的是通用小标题，属正常）：{same}")

    print("\n=== 3. 专属小标题只被自己的词表认出 ===")
    for frag, owner, slot in (
        ("三 试剂与仪器\n0.1000 mol/L NaOH 溶液、酚酞指示剂。\n", "基础化学实验", "步骤可复现性"),
        ("三 材料与方法\n洋葱、显微镜、载玻片。\n", "生物实验", "步骤可复现性"),
    ):
        for p in SC.PROFILE_KEYWORDS:
            got = slot in SC.locate_sections([], frag, p)
            expect = (p == owner)
            mark = "OK" if got == expect else ("误认!" if got else "漏认!")
            if got != expect:
                fail += 1
            print(f"   [{owner}] 的片段 / 用 {p:12s} -> {'认出' if got else '没认出'}  {mark}")
        print()

    print("结论：", "全部通过" if fail == 0 else f"{fail} 项异常，需要修")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
