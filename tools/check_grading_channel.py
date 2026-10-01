# -*- coding: utf-8 -*-
"""端到端回归：侧边栏填的通道，能不能真正驱动「评阅」链路？

跑法：python tools/check_grading_channel.py

背景（2026-09-24，用户截图报障）：
    侧边栏选了 DeepSeek、key 填好、界面绿字「已就绪」，
    一点「② 开始评阅」却报
    「RuntimeError: DEMO_MODE=true：已阻止调用真实模型。」
    根因是 pipeline 的 5 处 call_json 全都不传 api_key，学生自带的密钥
    从来没被传下去。

怎么做到「不需要真 key、也不联网」还能精确区分修复前/后：
    用「自定义」通道把 base_url 指向一个**故意打不通**的本地端口。
    · 修复前：马上抛 DEMO_MODE 的 RuntimeError（压根没走到网络）
    · 修复后：真的发起网络调用，报的是连不上之类的调用失败
    所以断言就是「错误里不许再出现 DEMO_MODE，且必须是真的调用失败」。
"""
import os
import sys

os.environ["DEMO_MODE"] = "true"          # 故意保留现场条件：DEMO_MODE 开着

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402
import providers  # noqa: E402

DEAD = "http://127.0.0.1:1/v1"            # 保留端口，必然连不上


def _sidebar_text(at):
    parts = []
    for w in at.sidebar.markdown:
        parts.append(str(w.value))
    for w in at.sidebar.caption:
        parts.append(str(w.value))
    for w in at.sidebar.warning:
        parts.append(str(w.value))
    for w in at.sidebar.info:
        parts.append(str(w.value))
    return "\n".join(parts)


def main():
    failed = []

    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=300).run()

    # 切到「自定义」通道，填一个打不通的地址（不需要真 key）
    at.selectbox(key="llm_channel_name").select(
        providers.get("custom")["name"]).run()
    at.text_input(key="llm_bu").set_value(DEAD).run()
    at.text_input(key="llm_mdl").set_value("dead-model").run()
    at.text_input(key="llm_key_input").set_value("sk-self-provided-key").run()

    # ① 界面提示必须说真话：不能再出现「评阅不会调用真实模型」这种误导
    txt = _sidebar_text(at)
    print("─" * 60)
    print("侧边栏提示：")
    for line in txt.splitlines():
        if line.strip():
            print("   " + line.strip()[:100])

    if "评阅与自检都会用上面这个通道调用模型" not in txt:
        failed.append("侧边栏没有打出「会用这个通道调用模型」的确认")
    else:
        print("✓ 侧边栏已明确告知：评阅会走用户自己填的通道")

    # ② 点「生成评分点」：必须真的走到网络（而不是被 DEMO_MODE 拦住）
    for b in at.button:
        if "生成评分点" in b.label:
            b.click().run()
            break

    errs = [str(e.value) for e in at.exception]
    err_text = "\n".join(errs)
    shown = [str(e.value) for e in at.error]
    blob = err_text + "\n" + "\n".join(shown)
    print("─" * 60)
    print("点击「生成评分点」后的报错（截断）：")
    print("   " + (blob.strip().splitlines() or ["（无）"])[0][:160])

    if "DEMO_MODE" in blob:
        failed.append("仍然被 DEMO_MODE 拦住 —— 学生自带的密钥没有透传到调用层")
    else:
        print("✓ 不再被 DEMO_MODE 拦截（密钥已透传到调用层）")

    if not blob.strip():
        failed.append("没有任何报错也没有成功 —— 期望是「连不上死地址」的调用失败")
    elif not any(k in blob for k in
                 ("Connection", "连接", "APIConnection", "生成失败", "Timeout", "超时")):
        failed.append(f"报错不像真实的调用失败，请人工看一下：{blob[:200]}")
    else:
        print("✓ 报错来自真实网络调用失败（符合预期：地址是故意的死端口）")

    print("─" * 60)
    if failed:
        for f in failed:
            print("✗ " + f)
        print("CHECK FAILED")
        return 1
    print("CHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
