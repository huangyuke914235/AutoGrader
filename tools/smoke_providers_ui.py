# -*- coding: utf-8 -*-
"""逐个切换「模型接入」通道，用真实 Streamlit 运行时验证界面不崩。

跑法：python tools/smoke_providers_ui.py

为什么要单独跑：侧边栏的通道选择器会按所选供应商动态渲染不同的输入控件
（离线无控件 / 预设只填 key / 自定义要填三项），每种组合都是一条独立的渲染路径。
只测默认通道会漏掉「切到自定义后报错」这类只在特定分支出现的问题。

注意：本脚本只验证渲染，**不发起任何真实模型调用**。
"""
import os
import sys

os.environ["DEMO_MODE"] = "true"
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/v1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402
import providers  # noqa: E402

# 挑几个有代表性的分支：无控件 / 填 key / 不填 key / 全自定义
PROBE = ["offline", "deepseek", "qwen", "ollama", "custom"]


def main():
    failed = 0
    for pid in PROBE:
        p = providers.get(pid)
        at = AppTest.from_file(os.path.join(ROOT, "app.py"),
                               default_timeout=300).run()
        try:
            at.selectbox(key="llm_channel_name").select(p["name"]).run()
        except Exception as e:  # noqa: BLE001
            print(f"  {pid:10s} 切换失败：{type(e).__name__}: {e}")
            failed += 1
            continue
        errs = [e.value for e in at.exception]
        cfg = at.session_state.get("llm_channel_name")
        if errs:
            print(f"  {pid:10s} ✗ 渲染异常：{errs[:2]}")
            failed += 1
        else:
            # 顺带确认：需要 key 的通道确实渲染出了密钥输入框（代码里是 password 类型，
            # AppTest 的元素类型字段看不出来，但参数已在 render_llm_picker 里写明）
            labels = [i.label for i in at.text_input]
            has_key_box = any("API Key" in lbl for lbl in labels)
            need = providers.get(pid)["needs_key"]
            if has_key_box != need:
                print(f"  {pid:10s} ✗ 密钥输入框与 needs_key 不一致")
                failed += 1
                continue
            print(f"  {pid:10s} ✓ 无异常  当前选择={cfg}  "
                  f"密钥框={'有' if has_key_box else '无（该通道不需要）'}")

    # 切换通道后再点一次体检，确认整条链路带着 llm_cfg 跑通
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()
    at.selectbox(key="llm_channel_name").select(
        providers.get("deepseek")["name"]).run()
    for b in at.button:
        if "开始体检" in b.label:
            b.click().run()
            break
    errs = [e.value for e in at.exception]
    if errs:
        print(f"  切到 deepseek 后体检 ✗ {errs[:1]}")
        failed += 1
    else:
        print("  切到 deepseek 后体检 ✓ 无异常（密钥为空，4 项应为未检测）")

    print("SMOKE OK" if failed == 0 else f"SMOKE FAILED（{failed} 项）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
