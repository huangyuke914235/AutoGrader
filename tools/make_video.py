# -*- coding: utf-8 -*-
"""全自动视频合成：真实截图 + PPT 原页 + 中文配音 + 字幕 → MP4

按 docs/视频分镜脚本.md 的 10 个镜头自动组装，不手工剪。

素材全部是真实产物，不做摆拍：
    - 应用界面：tools/record_app.py 用 Playwright 驱动本地应用真实抓取（真实模型调用）
    - PPT 页面：直接从 AutoGrader-参赛答辩.pptx 渲染（同一份要提交的文件）
    - 开头/结尾：Pillow 现场生成
    - 配音：edge-tts 中文语音；**自动配速**保证成片不超过 180 秒
    - 字幕：烧进画面，评委用任何播放器都能看到

用法：
    python tools/make_video.py --dry-run     # 只生成素材与配音，看时长预估
    python tools/make_video.py               # 完整合成
"""
import os
import sys
import json
import argparse
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "video_out")
SHOTS = os.path.join(OUT, "shots")
FRAMES = os.path.join(OUT, "frames")
TTS = os.path.join(OUT, "tts")
SEGS = os.path.join(OUT, "segs")
PPTX = os.path.join(ROOT, "AutoGrader-参赛答辩.pptx")
FINAL = os.path.join(ROOT, "AutoGrader-3分钟演示.mp4")

W, H, FPS = 1920, 1080, 30
HARD_LIMIT = 174.0                 # 留 6 秒余量，成片务必 ≤180s
VOICE = "zh-CN-YunxiNeural"        # 云希：男声讲述感
RATE_LADDER = ["+0%", "+8%", "+15%", "+22%", "+30%"]
FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_B = "C:/Windows/Fonts/msyhbd.ttc"

BG, FG, MUTED, ACCENT = (14, 17, 23), (238, 240, 243), (150, 158, 170), (255, 90, 74)

# ---------------------------------------------------------------------------
# 10 个镜头的口播与画面（多画面按解说时间切片，不用硬切假象）
# ---------------------------------------------------------------------------
BEATS = [
    dict(id="01_hook", narration=
         "一个助教批改六十份实验报告，要花掉差不多十个小时；更麻烦的是，"
         "同一份报告、同一套标准，过一会儿再改，分数可能就不一样了。",
         parts=[dict(kind="graphic", style="hook")], min_dur=8.5),

    dict(id="02_pain", narration=
         "所以我们没有做「让 AI 打个分」——那只是把黑箱换成一个更大的黑箱。"
         "我们做的是：把老师的评分标准，变成一条可核查、可溯源的判定流水线。",
         parts=[dict(kind="ppt", slide=2, subtitle="不做「让 AI 打个分」", tag="定位")],
         min_dur=7.5),

    dict(id="03_upload", narration=
         "先看真实操作。左边是一份已经脱敏的实验报告，右边是老师用大白话写的评分标准。"
         "展开这里是原始版面，方便对照报告里的表格和截图——注意，"
         "图片内容系统读不到，也不会成为评分依据，它只负责让人看得见。",
         parts=[
             dict(kind="shot", shot="tab1_载入样本", w=0.55,
                  subtitle="报告已脱敏｜评分标准用自然语言写，总分 100", tag="真实操作"),
             dict(kind="shot", shot="tab1_版面对照", w=0.45,
                  subtitle="原始版面对照（PDF 渲染）｜图片内容不参与自动判定", tag="能力边界"),
         ], min_dur=15.0),

    dict(id="04_rubric", narration=
         "第一步，把它拆成可以单独判定的原子评分点。这里有一个「人在回路」的关口："
         "老师可以改分值、可以增删，改完立刻生效——系统不替老师把标准定死。",
         parts=[dict(kind="shot", shot="tab3_评分点明细",
                     subtitle="人在回路 · 关口一：评分标准由老师定", tag="R · 评分点原子化")],
         min_dur=11.0),

    dict(id="05_run", narration=
         "第二步，逐个评分点判定。模型每判一个点，都必须回引报告原文——"
         "不是它自己说「有」，而是必须在正文里找到那句话。",
         parts=[
             dict(kind="shot", shot="tab1_评阅进行中", w=0.5, cut_note=True,
                  subtitle="逐点判定中｜真实模型调用", tag="A · 证据锚定判定"),
             dict(kind="shot", shot="tab1_评阅完成", w=0.5,
                  subtitle="评阅完成：总分、耗时、待复核项一目了然", tag="A · 证据锚定判定"),
         ], min_dur=10.0),

    dict(id="06_detail", narration=
         "出分之后是最关键的一页：左边是报告原文，命中的证据已经高亮；"
         "右边是每个评分点的判定、置信度和理由。任何一个分数，都能追到原文里那一行字。",
         parts=[
             dict(kind="shot", shot="tab2_原文与卡片", w=0.5,
                  subtitle="左：原文　右：逐点判定卡片", tag="可溯源"),
             dict(kind="shot", shot="tab2_证据与改分入口", w=0.5,
                  subtitle="展开评分点：证据引用 + 人工改分入口", tag="可溯源"),
         ], min_dur=13.0),

    dict(id="07_override", narration=
         "点「定位」，原文直接跳到那句话。如果老师不认同，可以直接改分、并留下理由——"
         "最终拍板的永远是人，AI 只负责给出带依据的初判。",
         parts=[
             dict(kind="shot", shot="tab2_定位跳转", w=1.0,
                  subtitle="点「定位」→ 原文对应句高亮｜改分必须填理由、全程留痕",
                  tag="人在回路 · 关口二"),
         ], min_dur=11.0),

    dict(id="08_bench", narration=
         "我们还给自己安排了一次闭卷考试：九份报告由不写代码的队友独立人工打分、封存，"
         "评测当天才拆。第一次跑出来是三十一点六七分，查出模型只看到报告的百分之七；"
         "修好之后是二十点四四分；再修掉两个工程缺陷，跑完整流水线是六点七分左右——"
         "同一份代码跑两次，六点八九和六点六七。这些修复都发生在解封之后，"
         "所以这一轮不算严格盲测：我们把修复前的二十点四四保留为对外主指标。",
         parts=[
             dict(kind="shot", shot="home_自评测", w=0.62,
                  subtitle="四次完整评测并列：20.44 → 6.89 / 6.67（区间）", tag="自评测 · 可复现"),
             dict(kind="shot", shot="tab5_批量测试", w=0.38,
                  subtitle="批量评阅 / 同一份重复评阅（一致性）", tag="评测工具"),
         ], min_dur=24.0),

    dict(id="09_rag", narration=
         "整条链路叫 RAG-E：评分点原子化、证据锚定判定、一致性守卫、反馈生成。"
         "三条工程约束是——分数由代码加总、无证据的判定不算数、不确定就交给人。",
         parts=[dict(kind="ppt", slide=3, subtitle="R → A → G → E　每层都有确定性代码兜底",
                     tag="方法论")], min_dur=11.0),

    dict(id="10_team", narration=
         "我们是深圳大学的两名本科生。代码、排查记录和这份自评测，全部开源在仓库里，欢迎复现。",
         parts=[dict(kind="graphic", style="team")], min_dur=7.0),
]

TEAM_LINES = [
    "AutoGrader · 实验报告智能评阅平台",
    "github.com/huangyuke914235/AutoGrader",
    "深圳大学 · 黄宇科（主程）／张镒川（产品与材料）",
    "在线 Demo：autograder-szu.streamlit.app",
]


def log(m):
    print(f"[video] {m}", flush=True)


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if p.returncode != 0:
        log("命令失败：" + " ".join(str(c) for c in cmd)[:300])
        log((p.stderr or "")[-1200:])
        raise RuntimeError("ffmpeg 失败")
    return p


# ---------------- 画面 ----------------
def _font(size, bold=False):
    from PIL import ImageFont
    return ImageFont.truetype(FONT_B if bold else FONT, size)


def _wrap(text, font, max_w, draw):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def make_graphic(style, out_png):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    if style == "hook":
        y = 250
        for i, line in enumerate(["批改 60 份实验报告 ≈ 10 小时",
                                  "同一份报告换个时间再改，10 次里有 2 次判定会漂"]):
            f = _font(66 if i == 0 else 44, bold=(i == 0))
            for ln in _wrap(line, f, W - 340, d):
                d.text((170, y), ln, font=f, fill=FG if i == 0 else MUTED)
                y += int(f.size * 1.35)
            y += 28
        d.text((170, 620), "数据来源：同一份报告连测 10 次（仓库 tools/stability_test.py）",
               font=_font(28), fill=(112, 120, 132))
    else:
        y = 300
        for i, line in enumerate(TEAM_LINES):
            f = _font(58 if i == 0 else 38, bold=(i == 0))
            for ln in _wrap(line, f, W - 340, d):
                d.text((170, y), ln, font=f, fill=FG if i == 0 else MUTED)
                y += int(f.size * 1.4)
            y += 24
    img.save(out_png)
    return out_png


def make_ppt_frame(slide_no, out_png):
    """把真实 PPT 的某一页渲染成图（读的是要提交的那份 pptx）"""
    from pptx import Presentation
    from PIL import Image, ImageDraw
    prs = Presentation(PPTX)
    slide = prs.slides[slide_no - 1]
    inch_px = 144
    sx = W / (prs.slide_width / 914400 * inch_px)
    sy = H / (prs.slide_height / 914400 * inch_px)

    def X(v):
        return int(v / 914400 * inch_px * sx)

    def Y(v):
        return int(v / 914400 * inch_px * sy)

    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for shape in slide.shapes:
        fill = None
        try:
            if shape.fill.type == 1:
                c = shape.fill.fore_color.rgb
                fill = (c[0], c[1], c[2])
        except Exception:
            fill = None
        x0, y0, x1, y1 = X(shape.left), Y(shape.top), X(shape.left + shape.width), Y(shape.top + shape.height)
        if fill and "AUTO_SHAPE" in str(shape.shape_type):
            d.rectangle([x0, y0, x1, y1], fill=fill)
        if shape.has_text_frame and shape.text_frame.text.strip():
            ty = y0 + 4
            for para in shape.text_frame.paragraphs:
                txt = "".join(r.text for r in para.runs)
                if not txt.strip():
                    ty += 14
                    continue
                run = para.runs[0]
                size = int(run.font.size.pt * 2) if run.font.size else 30
                color = (40, 44, 52)
                try:
                    if run.font.color and run.font.color.rgb:
                        c = run.font.color.rgb
                        color = (c[0], c[1], c[2])
                except Exception:
                    pass
                f = _font(size, bold=bool(run.font.bold))
                for ln in _wrap(txt, f, max(60, x1 - x0 - 16), d):
                    if "CENTER" in str(para.alignment or ""):
                        d.text(((x0 + x1 - d.textlength(ln, font=f)) / 2, ty), ln, font=f, fill=color)
                    else:
                        d.text((x0 + 8, ty), ln, font=f, fill=color)
                    ty += int(size * 1.34)
    img.save(out_png)
    return out_png


def fit_shot(src, out_png, subtitle, tag, cut_note=False):
    """应用截图 → 1920×1080，底部烧字幕、右上角打标签"""
    from PIL import Image, ImageDraw
    im = Image.open(src).convert("RGB")
    scale = min(W / im.width, (H - 118) / im.height)
    im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (8, 10, 14))
    canvas.paste(im, ((W - im.width) // 2, 22))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, H - 94, W, H], fill=(10, 12, 16))
    f = _font(34)
    d.text(((W - d.textlength(subtitle, font=f)) / 2, H - 68), subtitle, font=f, fill=FG)
    if tag:
        ft = _font(26, bold=True)
        tw = d.textlength(tag, font=ft)
        d.rectangle([W - tw - 84, 32, W - 44, 82], fill=ACCENT)
        d.text((W - tw - 64, 44), tag, font=ft, fill=(255, 255, 255))
    if cut_note:
        note = "评阅过程实际耗时约 30 秒，此处为多帧节选"
        fn = _font(28)
        tw = d.textlength(note, font=fn)
        d.rectangle([(W - tw) / 2 - 24, 96, (W + tw) / 2 + 24, 148], fill=(40, 44, 54))
        d.text(((W - tw) / 2, 108), note, font=fn, fill=(250, 214, 120))
    canvas.save(out_png)
    return out_png


# ---------------- 配音 ----------------
def index_shots():
    """按文件名索引截图（NN_名称.png → 名称 → 路径）

    不依赖 manifest.json：录制脚本在浏览器收尾阶段可能被沙箱打断，
    而截图本身早已落盘，合成不该因为一个清单文件而失败。
    """
    import glob as _glob
    import re
    out = {}
    for p in sorted(_glob.glob(os.path.join(SHOTS, "*.png"))):
        base = os.path.basename(p)
        if base.startswith("_"):
            continue
        m = re.match(r"^\d+_(.+)\.png$", base)
        if m:
            out[m.group(1)] = p
    return out


def narrate(text, out_mp3, rate):
    import asyncio
    import edge_tts
    async def go():
        await edge_tts.Communicate(text, VOICE, rate=rate).save(out_mp3)
    asyncio.run(go())
    return out_mp3


def audio_dur(path):
    p = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    for line in (p.stderr or "").splitlines():
        if "Duration:" in line:
            t = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


# ---------------- 分段 / 合成 ----------------
def build_segment(frame, audio, start, dur, out_mp4):
    frames = max(2, int(dur * FPS))
    vf = (f"scale={int(W*1.12)}:{int(H*1.12)},"
          f"zoompan=z='min(zoom+0.00035,1.10)':d={frames}:"
          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},format=yuv420p")
    cmd = [ffmpeg_exe(), "-y", "-loop", "1", "-i", frame]
    if audio:
        cmd += ["-ss", f"{max(0,start):.3f}", "-t", f"{dur:.3f}", "-i", audio]
    cmd += ["-vf", vf, "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "medium",
            "-crf", "20", "-r", str(FPS)]
    cmd += ["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]
    cmd += [out_mp4]
    run(cmd)
    return out_mp4


def concat(seg_files, out_mp4):
    lst = os.path.join(SEGS, "_list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s in seg_files:
            f.write(f"file '{os.path.basename(s)}'\n")
    run([ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", lst,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_mp4])
    return out_mp4


def plan_total(rate):
    """按给定语速算出总时长（用于自动配速）"""
    total = 0.0
    for b in BEATS:
        mp3 = os.path.join(TTS, f"{b['id']}_{rate.replace('+','p').replace('%','')}.mp3")
        if not os.path.exists(mp3):
            narrate(b["narration"], mp3, rate)
        total += max(audio_dur(mp3) + 0.6, b.get("min_dur", 0.0))
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rate", default=None, help="固定语速；默认自动配速以满足 ≤180s")
    args = ap.parse_args()

    for d in (FRAMES, TTS, SEGS):
        os.makedirs(d, exist_ok=True)
    shots = index_shots()
    log(f"可用截图 {len(shots)} 张：{', '.join(sorted(shots))}")

    rate = args.rate
    if not rate:
        for r in RATE_LADDER:
            t = plan_total(r)
            log(f"语速 {r} → 预估总时长 {t:.1f}s")
            if t <= HARD_LIMIT:
                rate = r
                break
        rate = rate or RATE_LADDER[-1]
    log(f"采用语速 {rate}")

    seg_files, table, total = [], [], 0.0
    for b in BEATS:
        mp3 = os.path.join(TTS, f"{b['id']}_{rate.replace('+','p').replace('%','')}.mp3")
        narrate(b["narration"], mp3, rate)
        dur_a = audio_dur(mp3)
        dur = max(dur_a + 0.6, b.get("min_dur", 0.0))
        parts = b["parts"]
        weights = [p.get("w", 1.0 / len(parts)) for p in parts]
        sw = sum(weights)
        cursor = 0.0
        for k, (p, wt) in enumerate(zip(parts, weights)):
            pdur = dur * wt / sw
            frame = os.path.join(FRAMES, f"{b['id']}_{k}.png")
            if p["kind"] == "graphic":
                make_graphic(p["style"], frame)
                subtitle, tag, cut = b.get("subtitle", ""), "", False
            elif p["kind"] == "ppt":
                make_ppt_frame(p["slide"], frame)
                subtitle, tag, cut = p["subtitle"], p.get("tag", ""), False
            else:
                src = shots.get(p["shot"])
                if not src:
                    log(f"⚠ {b['id']} 缺少截图 {p['shot']}，跳过该画面")
                    continue
                fit_shot(src, frame, p["subtitle"], p.get("tag", ""), p.get("cut_note", False))
                subtitle, tag, cut = p["subtitle"], p.get("tag", ""), p.get("cut_note", False)
            if not args.dry_run:
                seg = build_segment(frame, mp3, cursor, pdur,
                                    os.path.join(SEGS, f"{b['id']}_{k}.mp4"))
                seg_files.append(seg)
            log(f"  {b['id']}[{k}] {pdur:.1f}s ← {p['kind']}:{p.get('shot', p.get('slide', p.get('style')))}"
                f"　字幕：{subtitle[:24]}")
            cursor += pdur
        table.append({"id": b["id"], "narration_sec": round(dur_a, 1), "segment_sec": round(dur, 1)})
        total += dur

    log(f"总时长 {total:.1f}s（上限 {HARD_LIMIT}s）")
    if args.dry_run:
        log("dry-run 结束（未编码）")
        return
    concat(seg_files, FINAL)
    log(f"✅ 成片 {FINAL}（{os.path.getsize(FINAL)/1024/1024:.1f} MB，{total:.1f}s）")
    json.dump({"rate": rate, "total_sec": round(total, 1), "voice": VOICE,
               "beats": table,
               "source": "tools/record_app.py 真实截图 + 真实 PPT + edge-tts 配音"},
              open(os.path.join(OUT, "video_manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
