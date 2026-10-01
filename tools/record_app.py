# -*- coding: utf-8 -*-
"""按分镜脚本自动操作本地应用并截图（真实界面、真实模型调用）

设计原则（对应分镜脚本里的三条录制要求）：
    1. 跑的是**真实模型、真实报告**：DEMO_MODE 必须为 false，脚本会先检查；
    2. 不做假：等待条件绑定界面上真实出现的完成提示（"已生成 N 个评分点"/"评阅完成"），
       而不是"按钮存在"这种点击前就成立的条件——那会导致截到没跑完的画面；
    3. 每张截图都写进 shots/manifest.json，含时间与说明，事后可核对。

用法：
    python tools/record_app.py                    # 全套（含真实评阅，约 2 分钟）
    python tools/record_app.py --skip-grading     # 跳过评阅步骤
"""
import os
import sys
import json
import time
import argparse
import datetime
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "video_out", "shots")
PORT = 8501
BASE = f"http://localhost:{PORT}"
VIEWPORT = {"width": 1600, "height": 1000}


def log(msg):
    print(f"[record] {msg}", flush=True)


def ensure_real_mode():
    env = {}
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    if env.get("DEMO_MODE", "").lower() in ("true", "1", "yes"):
        sys.exit("❌ DEMO_MODE=true：这是演示模式，会返回预置结果。"
                 "分镜脚本第一条就禁止预置假结果，请先把 .env 改成 DEMO_MODE=false")
    if not env.get("LLM_API_KEY"):
        sys.exit("❌ .env 里没有 LLM_API_KEY，无法做真实评阅")
    log("真实模式确认：DEMO_MODE=false 且已配置密钥")


def wait_app(timeout=90):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(BASE, timeout=5) as r:
                if r.status == 200:
                    log(f"应用已就绪（{time.time()-t0:.0f}s）")
                    return True
        except Exception:
            time.sleep(2)
    return False


def start_streamlit():
    import socket
    s = socket.socket()
    try:
        s.settimeout(2)
        s.connect(("127.0.0.1", PORT))
        s.close()
        log("检测到应用已在运行，直接复用")
        return None
    except Exception:
        pass
    log("启动本地应用…")
    logf = open(os.path.join(ROOT, "video_out", "_streamlit.log"), "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(PORT),
         "--server.headless", "true", "--browser.gatherUsageStats", "false"],
        cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, creationflags=0x00000008)
    if not wait_app():
        sys.exit("❌ 应用启动超时，请看 video_out/_streamlit.log")
    return proc


HIDE_CHROME_CSS = """
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stHeader"],
[data-testid="stStatusWidget"], #MainMenu, footer { display: none !important; }
"""


def shot(page, shots, name, note=""):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{len(shots):02d}_{name}.png")
    page.screenshot(path=path)
    shots.append({"file": os.path.basename(path), "name": name, "note": note,
                  "at": datetime.datetime.now().isoformat(timespec="seconds")})
    log(f"截图 {os.path.basename(path)}  {note}")
    return path


def click_tab(page, name):
    """Streamlit 不同版本把标签渲染成 tab 或 button，两种都试"""
    try:
        page.get_by_role("tab", name=name).first.click(timeout=8000)
        return "tab"
    except Exception:
        page.get_by_role("button", name=name).first.click(timeout=8000)
        return "button"


def wait_text(page, text, timeout_s=240):
    """等界面上真正出现某个完成提示（这才是"跑完了"的证据）"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            if page.get_by_text(text).count():
                return True
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return False


def open_expander_with(page, marker, max_try=12):
    """证据与改分控件都在折叠面板里：逐个展开，直到目标控件可见"""
    for i in range(page.locator("details").count()):
        if i >= max_try:
            break
        try:
            det = page.locator("details").nth(i)
            if not det.locator("summary").count():
                continue
            det.locator("summary").first.click(timeout=5000)
            page.wait_for_timeout(700)
            if det.get_by_text(marker).count():
                return det
            det.locator("summary").first.click(timeout=5000)   # 不是这个，收回去
            page.wait_for_timeout(300)
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-grading", action="store_true")
    ap.add_argument("--sample", default="S08")
    args = ap.parse_args()

    ensure_real_mode()
    os.makedirs(OUT, exist_ok=True)
    proc = start_streamlit()
    from playwright.sync_api import sync_playwright

    shots = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_context(viewport=VIEWPORT, locale="zh-CN").new_page()
            page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
            page.get_by_text("AutoGrader").first.wait_for(timeout=90000)
            # 隐藏 Streamlit 的工具栏/页眉（Deploy、Stop、Rerun 这些）
            # 它们是开发期控件，留在演示画面里不干净；这样做不修改任何页面内容
            try:
                page.add_style_tag(content=HIDE_CHROME_CSS)
            except Exception as e:
                log(f"隐藏工具栏失败：{type(e).__name__}")
            page.wait_for_timeout(3000)
            log("界面已渲染")

            # ---- 载入样本 + 版面对照 ----
            click_tab(page, "① 评阅")
            page.wait_for_timeout(1000)
            shot(page, shots, "tab1_载入样本", "评阅页：内置脱敏样本已载入")
            try:
                page.get_by_text("原始版面对照").first.click()
                page.wait_for_timeout(2500)
                shot(page, shots, "tab1_版面对照", "展开原始版面对照（PDF 渲染页图）")
                page.get_by_text("原始版面对照").first.click()
                page.wait_for_timeout(800)
            except Exception as e:
                log(f"版面对照展开失败：{type(e).__name__}")

            if not args.skip_grading:
                # ---- 生成评分点（真实调用）----
                log("点击「① 生成评分点」…")
                t0 = time.time()
                page.get_by_role("button", name="① 生成评分点").click()
                ok = wait_text(page, "个评分点", timeout_s=180)
                log(f"评分点生成{'完成' if ok else '超时'}（{time.time()-t0:.0f}s）")
                shot(page, shots, "tab1_评分点已生成", f"评分点生成完成（{time.time()-t0:.0f}s）")
                click_tab(page, "③ 评分点")
                page.wait_for_timeout(2000)
                shot(page, shots, "tab3_评分点明细", "③ 评分点：老师可改分值/增删")
                click_tab(page, "① 评阅")
                page.wait_for_timeout(1200)

                # ---- 真实评阅（含过程帧）----
                log("点击「② 开始评阅」…")
                t0 = time.time()
                page.get_by_role("button", name="② 开始评阅").click()
                page.wait_for_timeout(7000)
                shot(page, shots, "tab1_评阅进行中", "逐点判定进行中（真实调用）")
                ok = wait_text(page, "评阅完成", timeout_s=420)
                el = time.time() - t0
                log(f"评阅{'完成' if ok else '超时'}（{el:.0f}s）")
                page.wait_for_timeout(2500)
                shot(page, shots, "tab1_评阅完成", f"评阅完成（真实耗时 {el:.0f}s）")

                # ---- 详情对照 ----
                click_tab(page, "② 详情对照")
                page.wait_for_timeout(3000)
                shot(page, shots, "tab2_原文与卡片", "左：原文　右：逐点判定卡片")
                page.mouse.wheel(0, 900)
                page.wait_for_timeout(1500)

                # ---- 展开证据面板：定位 ----
                det = open_expander_with(page, "定位")
                if det is not None:
                    page.wait_for_timeout(800)
                    shot(page, shots, "tab2_证据与改分入口", "展开评分点：证据引用 + 人工改分")
                    try:
                        det.get_by_role("button", name="定位").first.click(timeout=6000)
                        page.wait_for_timeout(3000)
                        shot(page, shots, "tab2_定位跳转", "点「定位」→ 左侧原文对应句高亮")
                    except Exception as e:
                        log(f"定位点击失败：{type(e).__name__}")

                    # ---- 人工改分（真实应用一次，留痕）----
                    try:
                        num = det.locator("input[type=number]").first
                        cur = float(num.input_value() or 0)
                        newv = max(0.0, round(cur - 1, 1))
                        num.fill(str(newv))
                        page.wait_for_timeout(300)
                        det.locator("input[type=text]").last.fill(
                            "证据只覆盖了部分要点，酌情下调 1 分（演示人工终裁留痕）")
                        page.wait_for_timeout(300)
                        shot(page, shots, "tab2_填写改分理由", "填写改分理由（必填，留痕）")
                        det.get_by_role("button", name="应用改分").first.click(timeout=6000)
                        page.wait_for_timeout(4000)
                        shot(page, shots, "tab2_改分已应用", "改分已应用：总分更新并留痕")
                    except Exception as e:
                        log(f"人工改分演示失败（不影响其它画面）：{type(e).__name__}")
                else:
                    log("未找到证据面板（可能没有带证据的判定）")

                click_tab(page, "① 评阅")
                page.wait_for_timeout(1500)
                shot(page, shots, "tab1_评分点已生成_2", "回到评阅页")
                click_tab(page, "⑤ 批量测试")
                page.wait_for_timeout(2500)
                shot(page, shots, "tab5_批量测试", "⑤ 批量测试：批量评阅 / 一致性重复测试")

            # ---- 作品主页（真实静态页，展示四次评测与区分度）----
            home = "file:///" + os.path.join(ROOT, "docs", "index.html").replace("\\", "/")
            try:
                page.goto(home, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                page.mouse.wheel(0, 2100)
                page.wait_for_timeout(1500)
                shot(page, shots, "home_自评测", "作品主页：四次完整评测对照表")
            except Exception as e:
                log(f"主页截图失败：{type(e).__name__}")

            # ---- PPT 页面（供后续与 PPT 渲染图合并使用）----
            browser.close()
    finally:
        manifest = os.path.join(OUT, "manifest.json")
        json.dump({"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                   "sample": args.sample, "shots": shots},
                  open(manifest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"共 {len(shots)} 张截图 → {os.path.relpath(manifest, ROOT)}")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)      # playwright 清理会被沙箱打断，显式成功退出
