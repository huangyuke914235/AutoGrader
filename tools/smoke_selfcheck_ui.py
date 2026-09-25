# -*- coding: utf-8 -*-
"""用 Streamlit 官方 AppTest 真实运行集成后的 app.py，验证新增的「⑥ 学生自检」页。

跑法：python tools/smoke_selfcheck_ui.py
说明：不连真实模型（DEMO_MODE=true），只验证界面与规则引擎链路不崩。
"""
import os
import sys

os.environ["DEMO_MODE"] = "true"
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/v1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402


def click(at, label):
    for b in at.button:
        if label in b.label:
            b.click().run()
            return True
    return False


def main():
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()
    print("初始渲染异常:", [e.value for e in at.exception])

    print("点击开始体检:", click(at, "开始体检"))
    errs = [e.value for e in at.exception]
    print("体检后异常:", errs if errs else "无")

    print("标签页:", [getattr(t, "label", "") for t in at.tabs])
    print("指标:", [(m.label, m.value) for m in at.metric])

    # 再体检一次，验证版本对比链路
    print("第二次体检:", click(at, "开始体检"))
    errs2 = [e.value for e in at.exception]
    print("二次异常:", errs2 if errs2 else "无")
    print("体检历史条数:", len(at.session_state.get("sc_history", [])))
    print("SMOKE OK" if not errs and not errs2 else "SMOKE FAILED")


if __name__ == "__main__":
    main()
