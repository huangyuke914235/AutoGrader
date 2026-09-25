# -*- coding: utf-8 -*-
"""用真实浏览器验证右上角框架 UI 是否已被汉化。

跑法：
    python tools/verify_i18n.py            # 需要服务已在 8502 端口运行

检查点：
1. 右上角工具栏（stToolbar）里不出现裸的 "Deploy"
2. 点开三点菜单（stMainMenu），菜单项不出现 "Report a bug" / "Settings" 等英文
3. 输出截图 tools/_i18n_check.png 供人工复核

注意：汉化脚本是注入在 iframe 组件里的 JS，AppTest 不执行 iframe，
所以这件事**只能在真实浏览器里验**。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

URL = os.environ.get("AG_URL", "http://localhost:8502")
OUT = os.path.join(ROOT, "tools", "_i18n_check.png")

EN_HINTS = ["Deploy", "Report a bug", "Get help", "Settings", "Clear cache",
            "Record a screencast", "Record screen", "Auto rerun",
            "Made with Streamlit"]


def _find_chromium() -> str:
    """本机已下载的 Chromium 可执行文件。

    为什么手动找：沙箱环境会拦截 playwright install 最后的锁文件清理，
    导致版本目录经常不齐；这里退而求其次，用已经落盘的那一份。
    """
    import glob
    home = os.path.expanduser(r"~\AppData\Local\ms-playwright")
    pats = [
        os.path.join(home, "chromium_headless_shell-*",
                     "chrome-headless-shell-win64", "chrome-headless-shell.exe"),
        os.path.join(home, "chromium-*", "chrome-win64", "chrome.exe"),
    ]
    for pat in pats:
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
        kw = {"executable_path": exe} if exe else {}
        browser = p.chromium.launch(**kw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        # 给注入脚本一点生效时间（注入的 iframe 也要加载）
        page.wait_for_timeout(3000)

        print("== 1. 右上角工具栏 ==")
        try:
            toolbar = page.locator('[data-testid="stToolbar"]').inner_text()
        except Exception as e:  # noqa: BLE001
            print(f"  读不到工具栏：{type(e).__name__}: {e}")
            toolbar = ""
        print(f"  工具栏文本：{toolbar!r}")
        hit = [w for w in EN_HINTS if w in toolbar]
        if hit:
            print(f"  ✗ 仍有英文：{hit}")
            fail += 1
        else:
            print("  ✓ 无英文残留")

        print("== 2. 三点菜单 ==")
        opened = False
        for sel in ('[data-testid="stMainMenu"] button',
                    'button[aria-label="Main menu"]',
                    '[data-testid="stMainMenuPopover"] button'):
            try:
                page.locator(sel).first.click(timeout=3000)
                opened = True
                break
            except Exception:  # noqa: BLE001
                continue
        if not opened:
            print("  ⚠ 点不开菜单（选择器失效？），改为截图人工复核")
            fail += 1
        else:
            page.wait_for_timeout(1200)
            menu_text = page.inner_text("body")
            hit = [w for w in EN_HINTS if w in menu_text]
            if hit:
                print(f"  ✗ 菜单仍有英文：{hit}")
                fail += 1
            else:
                print("  ✓ 菜单无英文残留")

        # 3. 页脚
        print("== 3. 页脚 ==")
        try:
            footer = page.locator("footer").inner_text()
        except Exception:  # noqa: BLE001
            footer = ""
        print(f"  页脚文本：{footer!r}")
        if "Made with Streamlit" in footer:
            print("  ✗ 页脚未翻译")
            fail += 1
        else:
            print("  ✓ 页脚无英文残留")

        page.screenshot(path=OUT, full_page=False)
        print(f"截图：{OUT}")
        browser.close()

    print("结果：", "全部通过" if fail == 0 else f"{fail} 处未汉化")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
