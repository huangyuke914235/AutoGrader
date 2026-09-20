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
CASES = os.path.join(ROOT, "docs", "cases")
WINDOW = 500          # 每条证据左右各保留的字符数
MAX_TOTAL = 6000      # 窗口拼接后的总上限


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


def main():
    if not os.path.isdir(CASES):
        print("没有 docs/cases/ 目录，请先运行 tools/make_demo.py")
        sys.exit(1)
    n = 0
    for fn in sorted(os.listdir(CASES)):
        if not fn.endswith(".json") or fn.endswith(".public.json"):
            continue
        data = json.load(open(os.path.join(CASES, fn), encoding="utf-8"))
        if "_is_public" in data:
            continue
        data["full_text"] = build_public_text(data["full_text"], data["judgements"])
        data["_is_public"] = True
        data["_note"] = "正文已裁剪为证据片段窗口，仅用于公开演示"
        out = os.path.join(CASES, fn)
        json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  {fn}：已裁剪为 {len(data['full_text'])} 字窗口")
        n += 1
    print(f"\n完成 {n} 个案例。现在可以安全 Push 到公开仓库。")


if __name__ == "__main__":
    main()
