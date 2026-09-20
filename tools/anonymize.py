# -*- coding: utf-8 -*-
"""
实验报告脱敏（v2 · 文本级脱敏）

用法：
    source .venv/Scripts/activate
    python tools/anonymize.py

设计原则（隐私优先）：
- 原始文件只在本地 data/raw/（已 gitignore），**绝不提交**
- data/samples/ 里**只放脱敏后的纯文本**，不含原始 PDF/DOCX
- 本项目流水线只用纯文本，不需要原文件，这样最安全

做什么：
1. 把配置好的原始报告复制到 data/raw/
2. 提取纯文本
3. 文本级脱敏：学号 / 姓名 / 班级 / 电话 / 邮箱 / 已知姓名
4. 输出到 data/samples/S01.txt ... 并生成 meta.json
5. 扫描残留敏感信息并报告
"""
import os
import re
import json
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.expanduser("~/Desktop")
RAW = os.path.join(ROOT, "data", "raw")
SAMPLES = os.path.join(ROOT, "data", "samples")

# ====== TODO: 需要增删样本就改这个列表 ======
SOURCES = [
    "Java/202601-JavaPD-课程实验1-2025150266-黄宇科.pdf",
    "Java/深圳大学《Java程序设计》课程实验1 完整报告.docx",
    "nlp小测2/RNN字符级文本生成_提交材料/RNN字符级文本生成实验报告.docx",
    "nlp期末/实验报告_对话问答回避检测与跨场景迁移分析_已完成.docx",
    "数据库/实验报告1_SQL的DDL语言和单表查询_已完成.docx",
    "数据库/实验1&2数据库应用实验（vastbase）.docx",
    "三维智能导论/树/2099154099张三Homework1.docx",
    "数据库/报告渲染QA3/report.pdf",
    "nlp期末/report_render_1/report.pdf",
    "nlp小测2/_work/report_final.pdf",
]

KNOWN_NAMES = ["黄宇科", "张三"]   # TODO: 若样本里出现你的姓名，加到这里

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

# 注意：CHECKERS 要能区分"残留的真信息"与"我们自己写的占位符"，否则会一直误报
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


def redact(text):
    hits = {}
    for label, pat, repl in PATTERNS:
        found = pat.findall(text)
        if found:
            hits[label] = len(found)
            text = pat.sub(repl, text)
    for nm in KNOWN_NAMES:
        c = text.count(nm)
        if c:
            hits[f"已知姓名({nm})"] = c
            text = text.replace(nm, "【姓名】")
    # 清理多余空白，保留段落
    text = re.sub(r"[ \t]{3,}", "  ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip(), hits


def main():
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(SAMPLES, exist_ok=True)

    meta = []
    idx = 0
    print("=" * 66)
    for rel in SOURCES:
        src = os.path.join(DESKTOP, rel)
        if not os.path.exists(src):
            print(f"  [跳过] 找不到 {rel}")
            continue
        idx += 1
        anon = f"S{idx:02d}"
        ext = os.path.splitext(rel)[1].lower()

        # 原文件只留本地
        shutil.copy2(src, os.path.join(RAW, anon + ext))

        raw_text = extract_text(src)
        if len(raw_text.strip()) < 100:
            print(f"  [跳过] {anon} 文本过短（{len(raw_text)} 字），可能是扫描件")
            idx -= 1
            continue

        clean, hits = redact(raw_text)
        out = os.path.join(SAMPLES, anon + ".txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(clean)

        # 残留检查
        rest = {k: len(v.findall(clean)) for k, v in CHECKERS.items() if v.findall(clean)}
        hit_str = "、".join(f"{k}×{v}" for k, v in hits.items()) or "无"
        print(f"  {anon}  {len(clean):>6} 字   脱敏:{hit_str}")
        if rest:
            print(f"        ⚠ 残留 -> {'、'.join(f'{k}×{v}' for k, v in rest.items())}")
        meta.append({"report_id": anon, "original": os.path.basename(rel),
                     "chars": len(clean), "redacted": hits, "residual": rest})

    with open(os.path.join(SAMPLES, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("=" * 66)
    print(f"完成：{len(meta)} 份脱敏文本 -> data/samples/")
    print("原始文件留在 data/raw/（已 gitignore），永远不会提交")


if __name__ == "__main__":
    main()
