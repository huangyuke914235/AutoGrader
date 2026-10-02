# -*- coding: utf-8 -*-
"""真机验证：多模态 OCR 端到端（会真实调用模型、消耗少量额度）

    python tools/verify_ocr_live.py

做三件事：
1. 读 .env 里的密钥与模型，先打印脱敏后的配置（不打印密钥本身）
2. 对 tools/_ocr_test.pdf 逐页读图，打印每页转录结果
3. 逐条核对**只有截图里才有**的关键数值是否被读出来 —— 这是"OCR 真的有用"的判据

关键判据不是"有没有返回文本"，而是：截图里的余额数字、操作次数、失败原因
这些**正文里根本没有**的信息，是否出现在了转录结果里。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ocr
from llm import get_env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(ROOT, "tools", "_ocr_test.pdf")

# 这些字符串只在截图里出现，正文文本层里没有（由 make_ocr_test_pdf.py 保证）
MUST_FIND = [
    "1500.00",        # 存入后的余额
    "1200.00",        # 取款后的余额
    "余额不足",        # 失败原因
    "成功 3 次",       # 统计
]


def main():
    if not os.path.exists(PDF):
        print(f"缺少测试文件：{PDF}\n请先跑 python tools/make_ocr_test_pdf.py")
        return 1

    key = get_env("LLM_API_KEY", "")
    model = get_env("LLM_MODEL", "deepseek-flash")
    base = get_env("LLM_BASE_URL", "")
    if not key:
        print("未配置 LLM_API_KEY：请写进 src/.env（该文件已 gitignore）")
        return 1
    print(f"配置：model={model}  base_url={base}  key={key[:3]}***{key[-4:]}")

    def prog(cur, total):
        print(f"  · 正在读取第 {cur}/{total} 页 …", flush=True)

    print(f"\n开始逐页读图：{os.path.relpath(PDF, ROOT)}")
    res = ocr.read_report_images(PDF, progress=prog)

    st = res["stats"]
    print(f"\n统计：{st['pages_read']} 页 / 转录 {st['chars']} 字 / "
          f"识别图片 {st['images']} 处 / 失败 {st['failed']} 页 / "
          f"耗时 {st['elapsed']}s / 模式 {st['mode']}")
    if res["errors"]:
        print("错误：")
        for e in res["errors"]:
            print("  -", e)

    print("\n" + "=" * 60)
    for p in res["pages"]:
        print(f"----- 第 {p['page']} 页 -----")
        print((p.get("text") or "").strip() or "（无转录文本）")
        for img in p.get("images") or []:
            print(f"  [{img['kind']}] {img['caption']}")
            if img.get("content"):
                print(f"      {img['content']}")
        if p.get("notes"):
            print(f"  （说明：{p['notes']}）")
    print("=" * 60)

    # 关键判据：截图独有信息是否被读出来
    blob = "\n".join((p.get("text") or "") + "\n" +
                     "\n".join((i.get("content") or "") + " " + (i.get("caption") or "")
                               for i in (p.get("images") or []))
                     for p in res["pages"])
    print("\n关键数值核对（这些只在截图里，正文读不到）：")
    hit = 0
    for token in MUST_FIND:
        ok = token in blob
        hit += ok
        print(f"  [{'命中' if ok else '未命中'}] {token}")
    print(f"\n结果：{hit}/{len(MUST_FIND)} 命中")
    if hit == len(MUST_FIND):
        print("→ OCR 链路可用：截图里的内容已经成为可引用正文")
        return 0
    print("→ 有未命中的关键数值，读图效果需要人工判断（见上面的转录原文）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
