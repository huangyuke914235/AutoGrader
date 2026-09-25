# -*- coding: utf-8 -*-
"""评阅耗时实测：量化「关复核 + 并发」到底快多少

为什么不用真模型跑：单次调用耗时受网络、平台排队、思考模式影响，
不同时段测出来的绝对值没有参考价值。所以这里固定**单次调用耗时**，
测的是**调用编排**（串行/并发、要不要复核）带来的差异 —— 这才是我
们能控制、也真正改到了的部分。

结论要按「倍数」读，不按秒读：真实耗时 = 表里倍数 × 你那次单次调用耗时。
"""
import re
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline                      # noqa: E402
from models import Rubric, RubricItem, Feedback   # noqa: E402

FULL = ("本报告写清了实验目的，并给出了完整的实现过程与运行结果数据，"
        "最后做了分析总结。")
GOOD = "完整的实现过程"

LATENCY = 0.30          # 模拟单次模型调用耗时（秒）
N_ITEMS = 8             # 默认评分点个数


def _items(n):
    return [RubricItem(id=f"r{i}", name=f"点{i}", criteria="是否有内容",
                       max_score=10) for i in range(n)]


def _fake_llm(system, user, schema, **kw):
    """替身：每次调用固定睡 LATENCY 秒，返回合模型的空壳结果。"""
    time.sleep(LATENCY)
    if schema is Feedback:
        return Feedback(summary="ok", per_item={}, suggestions=[])
    m = re.search(r"评分点：(\S+)", user)
    name = m.group(1) if m else "点0"
    idx = int(re.sub(r"\D", "", name) or 0)
    return schema.model_validate({
        "verdict": "hit", "score": (idx % 10) + 1, "confidence": 0.9,
        "reason": f"{name}的理由", "evidence": [{"quote": GOOD}],
    })


def bench(items, recheck, workers):
    t0 = time.time()
    pipeline.run_grading(FULL, [], raw_rubric="",
                         rubric=Rubric(items=_items(items)),
                         enable_recheck=recheck, judge_workers=workers)
    return time.time() - t0


def main():
    print("=" * 62)
    print(f"评阅耗时实测（单次调用固定 {LATENCY}s，{N_ITEMS} 个评分点）")
    print("=" * 62)
    pipeline.call_json = _fake_llm

    base_slow = bench(N_ITEMS, True, 1)        # 旧行为：串行 + 开复核
    rows = [
        ("串行 + 开复核（改动前）", True, 1),
        ("串行 + 关复核", False, 1),
        ("并发4 + 开复核", True, 4),
        ("并发4 + 关复核（当前默认）", False, 4),
        ("并发8 + 关复核", False, 8),
    ]
    print(f"{'配置':<26}{'实测':>9}{'调用次数':>10}{'相对改动前':>12}")
    print("-" * 62)
    for label, rc, w in rows:
        t = bench(N_ITEMS, rc, w)
        # 评分点是按钮①单独生成的，评阅这一步只有「判定(+复核)」+ 末尾一次反馈总结
        calls = N_ITEMS * (2 if rc else 1) + 1
        print(f"{label:<26}{t:>8.2f}s{calls:>10}{base_slow / t:>11.2f}×")

    print("-" * 62)
    print("读法：真实耗时 ≈ 表中倍数换算。例：若你的单次调用要 6 秒，")
    print(f"      改动前约 {base_slow / LATENCY * 6:.0f} 秒；")
    t_now = bench(N_ITEMS, False, 4)
    print(f"      当前默认（并发4 + 关复核）约 {t_now / LATENCY * 6:.0f} 秒。")
    print()
    print("另有一项不计入本表：Kimi k2.6 默认开着思考模式，会先写一大段推理")
    print("再作答 —— 已在 llm.fast_params() 里按型号关掉（只对 k2.6 生效）。")
    print("=" * 62)


if __name__ == "__main__":
    main()
