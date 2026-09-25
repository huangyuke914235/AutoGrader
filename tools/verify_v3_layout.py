# -*- coding: utf-8 -*-
"""用真实浏览器验证 v3 的两条布局/口径改动。

跑法：
    python tools/verify_v3_layout.py          # 需要服务已在 8502 端口运行

检查点：
1. 页面加载后**始终**有 6 个外层标签页 ① 评阅 … ⑥ 学生自检
   （v3 起按用户要求恢复教师端标签页常显，不再有教师模式开关）
2. 侧边栏**不再有**「教师模式」开关
3. 点「⑥ 学生自检」→「开始体检」后，体检总分按**百分制**输出
   （形如 "53.8 / 100"，绝不能出现 "/ 58" 之类的权重分母，
   也不能出现 v2 里那个二次折算的「相当于 xx%」）

注意：标签页在这个 Streamlit 版本里是 [role="tab"]，
     不是 button[data-baseweb="tab"]。
"""
import glob
import os
import re
import sys

URL = os.environ.get("AG_URL", "http://localhost:8502")
EXPECTED_TABS = ["① 评阅", "② 详情对照", "③ 评分点", "④ 导出", "⑤ 批量测试", "⑥ 学生自检"]
H1 = "AutoGrader · 实验报告智能评阅平台"


def _find_chromium() -> str:
    """本机已下载的 Chromium（沙箱会拦 playwright install 的收尾，用已落盘那份）。"""
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


def main() -> int:
    from playwright.sync_api import sync_playwright

    exe = _find_chromium()
    print(f"浏览器：{exe or '（未找到，用默认）'}")
    fail = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3000)

        # ---- 1. 标签页常显 ----
        tabs = [(e.inner_text() or "").strip().replace("\n", " ")
                for e in page.query_selector_all('[role="tab"]')]
        print(f"\n== 1. 外层标签页 ==\n  {tabs}")
        if tabs == EXPECTED_TABS:
            print("  ✓ 6 个标签页常显")
        else:
            print(f"  ✗ 应为 {EXPECTED_TABS}")
            fail += 1

        h1 = page.query_selector("h1")
        h1 = (h1.inner_text() or "").strip() if h1 else ""
        print(f"  标题：{h1!r}")
        if h1 != H1:
            print(f"  ✗ 标题应为 {H1!r}")
            fail += 1

        # ---- 2. 不再有教师模式开关 ----
        has_toggle = any(
            "教师模式" in (sw.get_attribute("aria-label") or "")
            for sw in page.query_selector_all('[role="switch"]'))
        print(f"\n== 2. 教师模式开关 ==\n  存在：{has_toggle}")
        if has_toggle:
            print("  ✗ v3 不该再有教师模式开关")
            fail += 1
        else:
            print("  ✓ 已移除")

        # ---- 3. 体检总分百分制 ----
        for t in page.query_selector_all('[role="tab"]'):
            if "学生自检" in (t.inner_text() or ""):
                t.click()
                break
        page.wait_for_timeout(1500)
        for btn in page.query_selector_all("button"):
            if "开始体检" in (btn.inner_text() or ""):
                btn.click()
                break
        page.wait_for_timeout(6000)

        metrics = []
        for m in page.query_selector_all('[data-testid="stMetric"]'):
            label_el = m.query_selector('[data-testid="stMetricLabel"]')
            value_el = m.query_selector('[data-testid="stMetricValue"]')
            label = (label_el.inner_text() or "").strip() if label_el else ""
            value = (value_el.inner_text() or "").strip() if value_el else ""
            metrics.append((label, value))
        print(f"\n== 3. 体检总分 ==\n  指标：{metrics}")

        total_value = next((v for l, v in metrics if "体检总分" in l), "")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)", total_value)
        if not m:
            print(f"  ✗ 体检总分格式异常：{total_value!r}")
            fail += 1
        elif m.group(2) != "100":
            print(f"  ✗ 分母应为 100（百分制），实际：{total_value!r}")
            fail += 1
        else:
            print(f"  ✓ 百分制输出：{total_value}")

        body = page.content()
        if "相当于" in body and "%" in body:
            print("  ✗ 页面仍有 v2 的「相当于 xx%」二次折算文案")
            fail += 1
        else:
            print("  ✓ 无二次折算文案")

        # ---- 4. 评阅页有「速度与质量」开关（Kimi 慢 → 让用户自己掌握快慢）----
        for t in page.query_selector_all('[role="tab"]'):
            if "评阅" in (t.inner_text() or ""):
                t.click()
                break
        page.wait_for_timeout(1500)
        print("\n== 4. 速度开关 ==")
        try:
            page.get_by_text("速度与质量").first.click()
            page.wait_for_timeout(1200)
        except Exception as e:
            print(f"  ✗ 找不到「速度与质量」折叠块：{type(e).__name__}")
            fail += 1
        body2 = page.content()
        for want, why in [("一致性复核", "复核开关"),
                          ("并发路数", "并发度滑块")]:
            if want in body2:
                print(f"  ✓ 有{why}")
            else:
                print(f"  ✗ 缺{why}（页面里没有「{want}」）")
                fail += 1
        if "预计模型调用" in body2:
            print("  ✓ 显示了预计调用次数")
        else:
            print("  · 未显示预计调用次数（还没生成评分点，属正常）")

        shot = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_v3_check.png")
        page.screenshot(path=shot)
        print(f"\n截图：{shot}")
        browser.close()

    print("\n结果：" + ("全部通过" if fail == 0 else f"{fail} 项未通过"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
