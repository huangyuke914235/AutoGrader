# -*- coding: utf-8 -*-
"""临时核对脚本：看规则引擎在真实样本上的判定是否合理（不属于测试套件）"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import parser as P  # noqa: E402
import selfcheck as SC  # noqa: E402


def show(path, experiment_type):
    full_text, sections, raw = P.parse_file(path, keep_lines=True)
    profile = SC.pick_profile(experiment_type)
    items = SC.rule_check(full_text, sections, profile, raw_text=raw)
    print(f"=== {os.path.basename(path)} [{profile}] 字数 {len(full_text)} "
          f"规则总分 {SC.compute_total(items)} ===")
    for it in items:
        problem = it.issues[0].problem[:52] if it.issues else ""
        print(f"   {it.name:8s} {it.score:6.1f} {it.status:3s} | {problem}")
    located = SC.locate_sections(sections, raw, profile)
    print("   识别章节:", "、".join(located) or "无")
    print()


if __name__ == "__main__":
    for name in ("S02.txt", "S03.txt", "S04.txt"):
        show(os.path.join(ROOT, "samples_demo", name), "Java程序设计实验")
    # 同一份 CS 报告故意用理工科词表跑一次，暴露词表选错的后果
    show(os.path.join(ROOT, "samples_demo", "S02.txt"), "大学物理实验")
