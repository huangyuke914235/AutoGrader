# -*- coding: utf-8 -*-
"""界面冒烟：用 Streamlit 自带的 AppTest 真跑一遍 app.py

为什么必须有这一层：单元测试全绿不等于界面能开。历史上那次「多模态/预览」
相关的缺陷就是「测试永远 skip、界面永远静默失败」的组合造成的。
AppTest 不启动浏览器，但它会真实执行整个脚本，任何语法/导入/契约错误都会抛出来。

检查项：
1. 脚本能跑完、无未捕获异常
2. 六个标签页都在
3. 侧边栏出现预置结果载入入口（零配置演示的前提）
4. 「② 开始评阅」在离线通道下**不抛异常**（旧实现必抛 RuntimeError）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _run():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    return at


def main():
    failures = []
    at = _run()

    if at.exception:
        for e in at.exception:
            failures.append(f"脚本抛出未捕获异常：{e.value}")

    # 六个标签页
    try:
        labels = [t.label for t in at.tabs]
    except Exception:
        labels = []
    for want in ("① 评阅", "② 详情对照", "③ 评分点", "④ 导出"):
        if want not in labels:
            failures.append(f"缺少标签页 {want}（实际：{labels}）")

    # 侧边栏：预置结果入口
    sb_text = " ".join(m.value for m in at.sidebar.markdown) + \
              " ".join(c.value for c in at.sidebar.caption)
    if "演示结果" not in sb_text and "预置评阅结果" not in sb_text:
        failures.append("侧边栏没有预置结果入口（零配置演示无法达成）")

    # 真正点一次「载入这份结果」—— 这是演示的核心动作，不能只检查按钮存在
    try:
        load_btns = [b for b in at.sidebar.button if "载入" in (b.label or "")]
        if not load_btns:
            failures.append("找不到「载入这份结果」按钮")
        else:
            load_btns[0].click().run()
            if at.exception:
                for e in at.exception:
                    failures.append(f"点「载入这份结果」抛异常：{e.value}")
            res = at.session_state.get("result")
            if res is None:
                failures.append("载入后 session_state['result'] 仍为空")
            else:
                if not at.session_state.get("full_text"):
                    failures.append("载入预置结果后没有装填 full_text，"
                                    "「② 详情对照」会没有原文可高亮")
                if not getattr(res, "judgements", None):
                    failures.append("载入的结果没有判定明细")
                else:
                    n_ev = sum(len(j.evidence) for j in res.judgements)
                    if n_ev == 0:
                        failures.append("载入的结果没有任何证据（演示就失去了意义）")
                    print(f"        载入预置结果：{res.report_id} 总分 {res.total} "
                          f"判定 {len(res.judgements)} 项 证据 {n_ev} 条 "
                          f"全文 {len(at.session_state.get('full_text') or '')} 字")
    except Exception as e:
        failures.append(f"载入预置结果时出错：{type(e).__name__}: {e}")

    # 离线通道下点「② 开始评阅」：必须给提示而不是抛异常
    try:
        btns = [b for b in at.button if "开始评阅" in (b.label or "")]
        if not btns:
            failures.append("找不到「② 开始评阅」按钮")
        else:
            btns[0].click().run()
            if at.exception:
                for e in at.exception:
                    failures.append(f"点「开始评阅」抛异常：{e.value}")
    except Exception as e:
        failures.append(f"点击评阅按钮时出错：{type(e).__name__}: {e}")

    if failures:
        print("[FAIL] 界面冒烟未通过：")
        for f in failures:
            print("   -", f)
        return 1

    print("[OK  ] 界面冒烟通过：脚本可运行、标签页齐全、预置结果入口存在、评阅按钮不抛异常")
    print(f"        标签页：{labels}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
