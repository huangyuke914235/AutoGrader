# -*- coding: utf-8 -*-
"""生成「可公开」版本的案例 JSON

为什么要这一步：
    docs/cases/ 里的原始案例带有**报告全文**。仓库一旦 Public（GitHub Pages 要求），
    全文就等于公开发布。课程作业原文公开可能带来不必要的麻烦。

这个脚本把 full_text 替换为只保留「每条证据 ±500 字」的窗口：
    - 高亮对照效果完全保留（演示用得上）
    - 暴露的正文大幅减少（只是不大的片段）

用法：
    python tools/publish_cases.py
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "cases_full")     # 全量中间产物（gitignore）
CASES = os.path.join(ROOT, "docs", "cases")        # 公开目录：只由本脚本写
WINDOW = 500          # 每条证据左右各保留的字符数
MAX_TOTAL = 6000      # 窗口拼接后的总上限
MANIFEST = os.path.join(CASES, "manifest.json")    # 主页案例列表的唯一来源


def build_public_text(full_text, judgements):
    spans = []
    for j in judgements:
        for e in j.get("evidence", []):
            q = e.get("quote", "")
            pos = full_text.find(q)
            if pos < 0 or len(q) < 6:
                continue
            a = max(0, pos - WINDOW)
            b = min(len(full_text), pos + len(q) + WINDOW)
            spans.append((a, b))

    if not spans:
        head = full_text[:MAX_TOTAL]
        return head + "\n\n……（未命中任何证据片段，仅展示报告开头）"

    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    parts = []
    used = 0
    for a, b in merged:
        if used >= MAX_TOTAL:
            break
        chunk = full_text[a:b]
        if len(chunk) > MAX_TOTAL - used:
            chunk = chunk[:MAX_TOTAL - used]
        prefix = "……" if a > 0 else ""
        parts.append(prefix + chunk + "……")
        used += len(chunk)
    return "\n\n".join(parts)


def check_public(data: dict):
    """公开产物的自检：任何一条不合格都不许写出，宁可失败也不许把全文发出去"""
    problems = []
    text = data.get("full_text", "")
    if not data.get("_is_public"):
        problems.append("缺少 _is_public 标记")
    if len(text) > MAX_TOTAL + 500:
        problems.append(f"正文 {len(text)} 字，超过公开上限")
    import re
    for label, pat in (("疑似学号", r"\b(?:19|20)\d{8,9}\b"),
                       ("疑似手机", r"\b1[3-9]\d{9}\b"),
                       ("疑似邮箱", r"[\w.+-]+@[\w-]+\.[\w.]+")):
        if re.search(pat, text):
            problems.append(f"命中{label}")
    return problems


def main():
    src = SRC if os.path.isdir(SRC) else CASES     # 兼容旧流程
    if not os.path.isdir(src):
        print("找不到源案例目录，请先运行 tools/make_demo.py")
        sys.exit(1)
    os.makedirs(CASES, exist_ok=True)

    n, published = 0, []
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".json") or fn == "manifest.json":
            continue
        raw = json.load(open(os.path.join(src, fn), encoding="utf-8"))
        raw["full_text"] = build_public_text(raw["full_text"], raw["judgements"])
        raw["_is_public"] = True
        raw["_note"] = "正文已裁剪为证据片段窗口，仅用于公开演示"

        problems = check_public(raw)
        if problems:
            print(f"  ❌ {fn} 未通过公开自检：{problems}")
            sys.exit(1)                     # 宁可失败，也不把不合规的内容写进公开目录

        json.dump(raw, open(os.path.join(CASES, fn), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        published.append(fn.replace(".json", ""))
        print(f"  {fn}：已裁剪为 {len(raw['full_text'])} 字窗口")
        n += 1

    json.dump({"cases": sorted(published), "generated_at": __import__("datetime")
               .datetime.now().isoformat(timespec="seconds")},
              open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n完成 {n} 个案例，并生成 manifest.json（主页案例列表从这里读）。")
    print("现在可以安全 Push 到公开仓库。")


if __name__ == "__main__":
    main()
