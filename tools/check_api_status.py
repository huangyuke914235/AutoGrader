# -*- coding: utf-8 -*-
"""API 接入体检：当前环境到底有哪些检查项在真跑

用法：
    python tools/check_api_status.py

回答的问题：
- 大模型 API 配了没有？配了能不能连通？
- 12 个检查项里，哪些能出分、哪些会显示「未检测」？
- 实际参与计分的权重是多少？（未检测项会从分母里剔除，不当 0 分）

部署排查 / 答辩演示前跑一次，避免把「只跑了 8 项」当成「跑了 12 项」。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import selfcheck as SC  # noqa: E402
from llm import demo_mode, get_env  # noqa: E402

RULE_W = sum(SC.CHECK_ITEMS[k][0] for k in SC.RULE_ITEMS)
AI_W = sum(SC.CHECK_ITEMS[k][0] for k in SC.AI_ITEMS)


def main() -> int:
    print("=== 1. API 配置 ===")
    key = get_env("LLM_API_KEY", "")
    base = get_env("LLM_BASE_URL", "")
    model = get_env("LLM_MODEL", "")
    demo = demo_mode()
    print(f"   DEMO_MODE   = {demo}")
    print(f"   LLM_API_KEY = {'已配置 (sk-***' + key[-4:] + ')' if key else '未配置'}")
    print(f"   LLM_BASE_URL= {base or '未配置'}")
    print(f"   LLM_MODEL   = {model or '未配置'}")

    if demo:
        verdict = "演示模式：不会调用真实模型，4 项 AI 检查恒定「未检测」"
    elif not key:
        verdict = "未配密钥：AI 检查会调用失败并降级为「未检测」"
    else:
        verdict = "已配置：12 项全部可检"
    print(f"   -> {verdict}")

    print("\n=== 2. 12 个检查项的来源 ===")
    for name, (w, eng) in SC.CHECK_ITEMS.items():
        ok = (eng == "rule")
        if eng == "ai":
            ok = (not demo and bool(key))
        print(f"   {name:16s} 权重{w:3d}  "
              f"{'规则(离线)' if eng == 'rule' else 'AI(需API)':12s}"
              f"{' 可出分' if ok else ' 未检测'}")

    active = RULE_W if (demo or not key) else RULE_W + AI_W
    print(f"\n   实际参与计分的权重：{active} / 100"
          f"（规则 {RULE_W} + AI {AI_W if active > RULE_W else 0}）")
    print("   注：未检测项从分母剔除，不会当成 0 分算给学生。")

    print("\n=== 3. 连通性实测 ===")
    if demo or not key:
        print("   跳过（当前配置下不会发起调用）")
        return 0
    try:
        from llm import call_json
        from models import SelfCheckPayload
        import prompts
        r = call_json(prompts.S5_SELFCHECK,
                      "请诊断：\n实验目的：验证欧姆定律。\n实验结论：测得电阻 100.2 Ω。",
                      SelfCheckPayload, temperature=0.2)
        print(f"   连通 OK，模型返回 {len(r.items)} 项诊断")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"   调用失败：{type(e).__name__}: {str(e)[:200]}")
        print("   -> 检查密钥是否正确、BASE_URL 是否可达、网络是否放行")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
