# -*- coding: utf-8 -*-
"""实验报告脱敏（v3 · 文本级脱敏，配置外置）

用法：
    python tools/anonymize.py                       # 读 tools/sources.local.json
    python tools/anonymize.py --sources 我的清单.json  # 指定别的配置文件

为什么配置必须外置：
    样本清单里包含**真实姓名、学号和本机目录结构**，一旦写进脚本就会被 git 跟踪，
    随之进入公开仓库——这是本项目踩过的最严重的一次隐私事故（2026-09-22 自查发现）。
    现在脚本里只留占位符，真实清单放在 `tools/sources.local.json`，
    该文件已加入 .gitignore，永远不提交。

设计原则（隐私优先）：
- 原始文件只在本地 data/raw/（已 gitignore），**绝不提交**
- data/samples/ 里**只放脱敏后的纯文本**，不含原始 PDF/DOCX
- 本项目流水线只用纯文本，不需要原文件，这样最安全

做什么：
1. 从本地配置读取样本清单（支持 desktop_root / sources / known_names）
2. 提取纯文本
3. 文本级脱敏：学号 / 姓名 / 班级 / 电话 / 邮箱 / 已知姓名
4. 输出到 data/samples/S01.txt ... 并生成 meta.json（只记脱敏后的文件名，不记原路径）
5. 扫描残留敏感信息并报告
"""
import argparse
import os
import re
import json
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
SAMPLES = os.path.join(ROOT, "data", "samples")
DEFAULT_CFG = os.path.join(ROOT, "tools", "sources.local.json")
EXAMPLE_CFG = os.path.join(ROOT, "tools", "sources.local.example.json")

# 脱敏规则与「已知姓名」一样，全部来自本地配置；这里只保留空默认值
SOURCES = []
KNOWN_NAMES = []


def load_config(path):
    """读取本地样本清单。配置不存在时给出明确指引，绝不回退到内置的真实路径。"""
    if not os.path.exists(path):
        print(f"找不到样本清单配置：{path}")
        print("请把 tools/sources.local.example.json 复制为 tools/sources.local.json，")
        print("填入你自己机器上的样本相对路径与已知姓名。该文件已在 .gitignore 中，不会被提交。")
        raise SystemExit(1)
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return (cfg.get("desktop_root") or os.path.expanduser("~/Desktop"),
            list(cfg.get("sources") or []),
            list(cfg.get("known_names") or []))


PATTERNS = [
    ("学号数字", re.compile(r"\b(?:19|20)\d{8,9}\b"), "【学号】"),
    ("学号标签", re.compile(r"学\s*号\s*[:：]?\s*[A-Za-z0-9\u4e00-\u9fa5\-_]{2,}"), "学号：【已脱敏】"),
    ("姓名标签", re.compile(r"姓\s*名\s*[:：]\s*\S{1,8}"), "姓名：【已脱敏】"),
    ("学生标签", re.compile(r"学生姓名\s*[:：]\s*\S{1,8}"), "学生姓名：【已脱敏】"),
    ("班级", re.compile(r"班\s*级\s*[:：]\s*\S{1,20}"), "班级：【已脱敏】"),
    ("手机", re.compile(r"\b1[3-9]\d{9}\b"), "【电话】"),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "【邮箱】"),
    ("QQ微信", re.compile(r"(QQ|微信|WeChat)\s*[:：]\s*\S{2,}"), "\\1：【已脱敏】"),
]

# CHECKERS 要能区分「残留的真信息」与「我们自己写的占位符」，否则会一直误报
CHECKERS = {
    "疑似学号": re.compile(r"\b(?:19|20)\d{8,9}\b"),
    "疑似手机": re.compile(r"\b1[3-9]\d{9}\b"),
    "疑似邮箱": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "疑似真姓名行": re.compile(r"(姓名|学号)\s*[:：]\s*(?!【已脱敏】|【学号】)\S+"),
}


def extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            import fitz
            doc = fitz.open(path)
            parts = [p.get_text() for p in doc]
            doc.close()
            return "\n".join(parts)
        if ext == ".docx":
            import docx
            d = docx.Document(path)
            return "\n" + "\n".join(p.text for p in d.paragraphs)
    except Exception as e:
        print(f"    [解析失败] {os.path.basename(path)}: {e}")
    return ""


def redact(text, known_names):
    hits = {}
    for label, pat, repl in PATTERNS:
        found = pat.findall(text)
        if found:
            hits[label] = len(found)
            text = pat.sub(repl, text)
    for nm in known_names:
        c = text.count(nm)
        if c:
            hits[f"已知姓名×{c}"] = c     # 只记数量，不把姓名本身写进 meta
            text = text.replace(nm, "【姓名】")
    text = re.sub(r"[ \t]{3,}", "  ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip(), hits


def main():
    ap = argparse.ArgumentParser(description="实验报告脱敏（配置外置，不写真实路径进源码）")
    ap.add_argument("--sources", default=DEFAULT_CFG, help="本地样本清单 JSON（不提交）")
    args = ap.parse_args()

    desktop_root, sources, known_names = load_config(args.sources)
    if not sources:
        print(f"配置里 sources 为空。请参考 {os.path.basename(EXAMPLE_CFG)} 填写。")
        raise SystemExit(1)

    os.makedirs(RAW, exist_ok=True)
    os.makedirs(SAMPLES, exist_ok=True)

    meta = []
    idx = 0
    print("=" * 66)
    for rel in sources:
        src = os.path.join(desktop_root, rel)
        if not os.path.exists(src):
            print(f"  [跳过] 找不到 {os.path.basename(rel)}")
            continue
        idx += 1
        anon = f"S{idx:02d}"
        ext = os.path.splitext(rel)[1].lower()

        shutil.copy2(src, os.path.join(RAW, anon + ext))   # 原文件只留本地

        raw_text = extract_text(src)
        if len(raw_text.strip()) < 100:
            print(f"  [跳过] {anon} 文本过短（{len(raw_text)} 字），可能是扫描件")
            idx -= 1
            continue

        clean, hits = redact(raw_text, known_names)
        out = os.path.join(SAMPLES, anon + ".txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(clean)

        rest = {k: len(v.findall(clean)) for k, v in CHECKERS.items() if v.findall(clean)}
        hit_str = "、".join(f"{k}×{v}" for k, v in hits.items()) or "无"
        print(f"  {anon}  {len(clean):>6} 字   脱敏:{hit_str}")
        if rest:
            print(f"        ⚠ 残留 -> {'、'.join(f'{k}×{v}' for k, v in rest.items())}")
        # meta 里只保留脱敏后的文件名（basename），不记录原始目录结构
        meta.append({"report_id": anon, "original": os.path.basename(rel),
                     "chars": len(clean), "redacted": hits, "residual": rest})

    with open(os.path.join(SAMPLES, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("=" * 66)
    print(f"完成：{len(meta)} 份脱敏文本 -> data/samples/")
    print("原始文件留在 data/raw/（已 gitignore），永远不会提交")


if __name__ == "__main__":
    main()
