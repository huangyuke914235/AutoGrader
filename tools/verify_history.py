# -*- coding: utf-8 -*-
"""端到端验证「体检历史持久化」这条链路（含两条不能退化的安全性断言）。

跑法：
    python tools/verify_history.py          # 需要服务已在 8502 端口运行

之所以要真跑浏览器，是因为这里要验的是**跨 rerun / 跨会话的状态是否留得住**：
单测能证明 history.py 读写正确，证明不了 Streamlit 那一层（档案名选择框、
回看按钮、rerun 之后的取值）把它接对了。实测中正是这一层踩到了
`StreamlitAPIException: cannot be modified after the widget is instantiated`。

检查点：
1. 体检两次后，本次新增恰好两条记录（不是一条被覆盖）
2. 落盘文件里**没有报告正文**、**没有密钥形态的字符串**
3. 「版本对比」页不再提示「再体检一次」，且给出逐项分差表
4. 档案面板里两条都能「回看」，且页面标注了「这不是最新一次体检」
5. 删除一条后只剩一条（若本机禁用删除则**显式跳过**，绝不当作通过）

计数口径：只统计**本次运行新增**的文件。
本机（沙箱）会拦截一切删除调用，上一次跑留下的记录删不掉，
拿总数对比会把残留算成「本次多写了文件」而误报 —— 这是假红灯，同样要避免。

这条脚本只用内置样本 + 关闭 AI（离线规则通道），不消耗任何额度，可反复跑。
"""
import glob
import os
import shutil
import sys

URL = os.environ.get("AG_URL", "http://localhost:8502")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "history")
ARCHIVE = "e2e档案"          # 与真实用户数据区分开
HOME = os.path.join(HIST, ARCHIVE)


def _files():
    """本档案下的体检记录（排除本脚本自己的探针文件，否则会被算成「本次多写了一条」）。"""
    out = set()
    for dp, _dn, fs in os.walk(HOME):
        out |= {os.path.join(dp, f) for f in fs
                if f.endswith(".json") and not f.startswith("_")}
    return out


def _can_delete() -> bool:
    """本机是否允许删除（沙箱里回收站不可用，删除会被 fail-closed 拦下）。"""
    try:
        os.makedirs(HIST, exist_ok=True)
        p = os.path.join(HIST, "_probe.json")      # 放根目录，不污染档案计数
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(p)
        return True
    except OSError as e:
        print(f"  · 删除能力探测失败：{str(e)[:80]}")
        return False


def _find_chromium() -> str:
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


def _robust_click(handle):
    """Streamlit 的 switch / button 里真正可见的是外层 label，
    直接点内层 input 会被「intercepts pointer events」挡住 ——
    真人点的是那层 label，所以退一步用 JS 触发同样的点击。"""
    try:
        handle.click(timeout=4000)
        return True
    except Exception:
        try:
            handle.evaluate("e => (e.closest('label') || e).click()")
            return True
        except Exception:
            return False


def _click_tab(page, name):
    for t in page.query_selector_all('[role="tab"]'):
        if name in (t.inner_text() or ""):
            return _robust_click(t)
    return False


def _click_button(page, text):
    for b in page.query_selector_all("button"):
        if text in (b.inner_text() or ""):
            return _robust_click(b)
    return False


def _open_archive_panel(page):
    for el in page.get_by_text("本机体检档案").all():
        try:
            el.click()
            page.wait_for_timeout(900)
            return True
        except Exception:
            continue
    return False


def _ensure_archive(page, name):
    """让当前会话锁定到指定档案名。

    档案名是「下拉框 + 文本框」的组合，两种情况都要处理：
    · 停在「＋ 新档案…」→ 文本框可见，直接填（fill 之后必须补 Enter 才触发 rerun）；
    · 已选过某个档案 → 文本框被折叠，要点开下拉改选。
    这一步做不对，两次体检会落进两个不同档案，后面所有断言都在假象上通过。
    """
    inp = page.query_selector('input[aria-label="新档案名"]')
    if inp is not None:
        inp.fill(name)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        return True
    sb = page.query_selector('input[aria-label="档案名（决定两次体检能否对比上）"]')
    if sb is None:
        return False
    sb.click()
    page.wait_for_timeout(500)
    for opt in page.get_by_role("option").all():
        if (opt.inner_text() or "").strip() == name:
            opt.click()
            page.wait_for_timeout(1000)
            return True
    return False


def main() -> int:
    from playwright.sync_api import sync_playwright

    fail = 0
    skipped = []
    before = _files()
    can_del = _can_delete()
    print(f"本机删除能力：{'可用' if can_del else '被拦截（相关项将跳过）'}")

    exe = _find_chromium()
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3000)

        # ---- 0. 进自检页，关掉 AI（离线规则通道：快、零额度、结果可复现）----
        _click_tab(page, "学生自检")
        page.wait_for_timeout(1200)
        for sw in page.query_selector_all('[role="switch"]'):
            if "AI 语义诊断" in (sw.get_attribute("aria-label") or ""):
                _robust_click(sw)
                break
        page.wait_for_timeout(1000)
        if not _ensure_archive(page, ARCHIVE):
            print("  ✗ 没能填上档案名")
            return 2

        # ---- 1. 连测两次 → 本次应新增恰好两条 ----
        for _ in range(2):
            if not _click_button(page, "开始体检"):
                print("  ✗ 找不到「开始体检」按钮")
                return 2
            page.wait_for_timeout(5000)
        new = sorted(_files() - before)
        print(f"\n== 1. 两次体检的落盘 ==\n  本次新增 {len(new)} 个文件")
        for f in new:
            print(f"    {os.path.basename(f)}")
        if len(new) == 2:
            print("  ✓ 两次各自成条，没有被互相覆盖")
        else:
            print("  ✗ 应恰好新增 2 条（同秒落盘被覆盖是这里最常见的退化）")
            fail += 1

        # ---- 2. 落盘内容的安全边界 ----
        blob = "".join(open(f, encoding="utf-8").read() for f in new)
        print("\n== 2. 存档内容边界 ==")
        if "sk-" in blob:
            print("  ✗ 存档里出现了疑似密钥")
            fail += 1
        else:
            print("  ✓ 无密钥")
        lens = [os.path.getsize(f) for f in new]
        print(f"  文件大小：{lens}")
        if lens and max(lens) < 20000:
            print("  ✓ 体积远小于报告正文（存档只含结论，不含正文）")
        else:
            print("  ✗ 单条存档过大，怀疑把正文也写进去了")
            fail += 1
        if ARCHIVE in blob:
            print("  ✓ 档案名已记录（可定位是哪份报告）")

        # ---- 3. 版本对比基于档案而非会话 ----
        _click_tab(page, "版本对比")
        page.wait_for_timeout(1200)
        body = page.content()
        print("\n== 3. 版本对比 ==")
        if "再体检一次" in body:
            print("  ✗ 仍提示「再体检一次」—— 说明没读到磁盘上的记录")
            fail += 1
        elif "检查项" in body:
            print("  ✓ 给出了逐项对比表")
        else:
            print("  ✗ 既没有对比表也没有提示，请人工核对")
            fail += 1
        if "相比上一次" in body:
            print("  ✓ 给出了与上一次的分差")
        # 折线是「整体在不在往上走」，表格是「这次具体改对了什么」——两件事都要有。
        # Streamlit 的 line_chart 渲染成 vega-lite 画布，所以既要查文案也要查图元。
        charts = page.query_selector_all('[data-testid="stVegaLiteChart"], canvas')
        print(f"  图表元件：{len(charts)} 个")
        if "体检总分趋势" in body and charts:
            print("  ✓ 有总分趋势折线")
        else:
            print("  ✗ 缺少总分趋势折线")
            fail += 1

        # ---- 4. 档案面板：两条都能回看 ----
        print("\n== 4. 档案面板 ==")
        _open_archive_panel(page)
        view_btns = [b for b in page.query_selector_all("button")
                     if (b.inner_text() or "").strip() == "回看"]
        print(f"  回看按钮：{len(view_btns)} 个")
        if len(view_btns) >= 2:
            print("  ✓ 两条历史都可以单独回看")
        else:
            print("  ✗ 回看按钮数量不足")
            fail += 1
        if view_btns:
            _robust_click(view_btns[0])
            page.wait_for_timeout(2500)
            body2 = page.content()
            if "这不是最新一次体检" in body2:
                print("  ✓ 回看时明确标注「不是最新一次」，不会被当成当前成绩")
            else:
                print("  ✗ 缺少该提示（回看结果会被误读成最新成绩）")
                fail += 1
            if _click_button(page, "回到最新一次"):
                page.wait_for_timeout(1500)
                print("  ✓ 可以退回最新一次")

        # ---- 5. 删除 ----
        print("\n== 5. 删除 ==")
        if not can_del:
            skipped.append("删除链路（本机禁用删除，需在有回收站的环境复跑）")
            print("  · 跳过：本机拦截删除调用，验了也只会得到假结论。"
                  "删除逻辑已在 tests/test_history.py 用 monkeypatch 覆盖。")
        else:
            _open_archive_panel(page)
            del_btns = [b for b in page.query_selector_all("button")
                        if (b.inner_text() or "").strip() == "删除"]
            if not del_btns:
                print("  ✗ 找不到「删除」按钮")
                fail += 1
            else:
                _robust_click(del_btns[0])
                page.wait_for_timeout(2000)
                if len(_files()) == len(new) - 1:
                    print("  ✓ 单条删除生效，且只删了目标那条")
                else:
                    print("  ✗ 删除后条数不对")
                    fail += 1

        shot = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_history_check.png")
        page.screenshot(path=shot)
        print(f"\n截图：{shot}")
        browser.close()

    try:                                   # 尽力清理；本机可能删不掉，不算失败
        shutil.rmtree(HOME)
    except OSError:
        print(f"· 未能清理测试档案（本机禁用删除）：{HOME}")
    print("\n结果：" + ("全部通过" if fail == 0 else f"{fail} 项未通过")
          + (f"；跳过 {len(skipped)} 项：{'；'.join(skipped)}" if skipped else ""))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
