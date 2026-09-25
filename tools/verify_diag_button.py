# -*- coding: utf-8 -*-
"""浏览器验证：侧边栏「测试连接」按钮 —— Kimi 通道修复后的界面侧

离线端到端（tools/verify_kimi_fix.py）已经证明 400 的根因修掉了，
但那验证的是 python 层。这里验证**界面真的把这个能力交到了用户手上**：

  1. 选 Kimi 通道后出现「测试连接」按钮（用户能自助分诊，不必再截图来回）；
  2. 通道摘要里显示的型号是 kimi-k2.6，不是已退役的 moonshot-v1；
  3. 六个标签页仍然常显（不能因为这次改动把 v3 的界面要求弄回去）。

为什么必须在真浏览器里跑：第一次 vals 隐藏/显示逻辑变了以后，
AppTest 这类不执行 JS 的框架是照不出真实渲染结果的（旧坑）。
"""
import glob
import os
import sys

URL = "http://localhost:8502"
KIMI = "Kimi 月之暗面"


def _find_chromium():
    home = os.path.expanduser(r"~\AppData\Local\ms-playwright")
    for pat in [
        os.path.join(home, "chromium_headless_shell-*",
                     "chrome-headless-shell-win64", "chrome-headless-shell.exe"),
        os.path.join(home, "chromium-*", "chrome-win64", "chrome.exe"),
    ]:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return ""


def main():
    from playwright.sync_api import sync_playwright

    exe = _find_chromium()
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" —— {detail}" if detail else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = browser.new_page(viewport={"width": 1400, "height": 1100})
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3000)

        # ---- 1. 六个标签页仍常显（v3 的硬要求，别改回去）----
        tabs = [e.inner_text().strip().replace("\n", " ")
                for e in page.query_selector_all('[role="tab"]')]
        check("①~⑥ 六个标签页常显", len(tabs) == 6, f"{len(tabs)} 个：{tabs}")

        # ---- 2. 切到 Kimi 通道 ----
        # 注意 selector：Streamlit 新版的下拉框不是原生 <select>，
        # 而是 baseweb 自定义控件（旧选择器 div select 会静默拿不到元素，
        # 然后脚本会误报成"功能没生效"）。同时必须限定在侧边栏内，
        # 因为「⑥ 学生自检」里还有学科下拉框。
        sidebar = page.locator('section[data-testid="stSidebar"]')
        box = sidebar.locator('[data-testid="stSelectbox"]').first
        check("找到侧边栏通道下拉框", box.count() > 0)
        if box.count() == 0:
            _finish(page, browser, checks, "sel-missing")
            return 1

        box.click()
        page.wait_for_timeout(800)
        opt = page.get_by_role("option", name=KIMI).first
        check("下拉列表里有 Kimi 通道", opt.count() > 0)
        if opt.count() == 0:
            _finish(page, browser, checks, "no-kimi")
            return 1
        opt.click()
        page.wait_for_timeout(2500)

        # ---- 3. 型号必须是现役的 kimi-k2.6 ----
        body = page.inner_text("body")
        check("界面显示现役型号 kimi-k2.6", "kimi-k2.6" in body)
        # 注意别写成 'moonshot-v1' not in body：那会误报 ——
        # 通道说明里本来就有一句「旧型号 moonshot-v1 已停服」的教育性提示。
        # 要断言的是「没有被当成实际型号用」，即「模型 `xxx`」这一处回显。
        check("没有把退役型号当成实际型号", "模型 `moonshot-v1" not in body,
              "通道回显的型号应为 kimi-k2.6")

        # ---- 4. 填一个占位 Key 后，测试连接按钮应当出现 ----
        # （没有 Key 时配置不合法，按钮不出现才是正确的 —— 不给人制造无效操作）
        check("未填 Key 时不出现测试按钮", "测试连接" not in body,
              "未填 Key 时按钮本就不该出现")

        pwd = sidebar.locator('input[type="password"]').first
        if pwd.count() > 0:
            pwd.fill("sk-probe-not-a-real-key-0000")
            # fill() 不会触发 Streamlit 重跑，必须再敲一下回车 / 失去焦点。
            # 少了这一步会误判成「按钮没出现」。
            pwd.press("Enter")
            page.wait_for_timeout(3000)

        body = page.inner_text("body")
        has_btn = "测试连接" in body
        check("填入 Key 后出现「测试连接」按钮", has_btn)

        if has_btn:
            # ---- 5. 真的点一次：应当把平台的原话带回来（这里是 401）----
            page.get_by_text("测试连接", exact=False).first.click()
            page.wait_for_timeout(12000)
            body = page.inner_text("body")
            check("诊断给出分诊结论", "拉取可用模型列表" in body,
                  "应显示带编号的逐步诊断")
            check("诊断提示两站端点", "api.moonshot.ai" in body,
                  "Kimi 国内/国际两平台的 Key 互不通用")

            # 只能检查**诊断正文**（code 块），不能检查整个页面：
            # 侧边栏回显的 `Key sk-****0000` 故意保留末 4 位，
            # 让人一眼确认自己填的是哪个 Key —— 那是设计意图，不是泄露。
            # 要守的是「会被复制粘贴出去的那份诊断报告」，两者标准不同，别混。
            blob = "\n".join(e.inner_text() for e in page.query_selector_all("pre"))
            check("诊断正文不泄露密钥", "0000" not in blob,
                  "诊断结果会被整段复制出去求助，一位都不该留")

        _finish(page, browser, checks, "diag")
        bad = [n for n, ok in checks if not ok]
        if bad:
            print(f"\n❌ {len(bad)}/{len(checks)} 项未通过：" + "、".join(bad))
            return 1
        print(f"\n✅ {len(checks)} 项全部通过。")
        return 0


def _finish(page, browser, checks, tag):
    try:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_{tag}.png")
        page.screenshot(path=d, full_page=True)
        print(f"截图：{d}")
    finally:
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
