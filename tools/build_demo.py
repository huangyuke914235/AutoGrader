# -*- coding: utf-8 -*-
"""构建「开箱即看」的预置演示结果（公开 Demo 不填 API Key 也能看到完整产品）

    python tools/build_demo.py                 # 生成全部内置样本的预置结果
    python tools/build_demo.py --live          # 用真实模型跑（需要 LLM_API_KEY）
    python tools/build_demo.py S02             # 只做某一份

两种模式，产出的文件结构完全相同：

- 默认（离线规则引擎）：零成本、可复现、不联网。评委打开网页就能看到
  「逐点判定 + 原文证据 + 代码加总 + 成绩单」这一整套形态，
  每个评分点都带真实证据、都标注了待人工复核。
- `--live`（真实模型 + 可选 OCR）：产出与线上完全一致的结果，用于对外演示
  「模型判定长什么样」。它需要密钥，所以只能在本地或 CI 里跑一次，
  产物提交进仓库。

为什么预置结果要带 `full_text`：
    「② 详情对照」页要靠全文做原文高亮，没有全文就只剩分数没有证据 —— 而证据
    才是这个产品要展示的东西。样本本身已经是脱敏的公开演示样本（samples_demo），
    把它的正文一并放进预置结果不引入任何新的隐私面。
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as P
import ocr as O
from pipeline import (run_offline_grading, default_rubric, save_demo_result,
                      run_ocr_enrichment, apply_ocr_to_runinfo, DEMO_DIRS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sample_dir() -> str:
    local = os.path.join(ROOT, "data", "samples")
    if os.path.isdir(local):
        return local
    return os.path.join(ROOT, "samples_demo")


def _out_dir() -> str:
    d = os.path.join(ROOT, DEMO_DIRS[0])       # data/demo
    os.makedirs(d, exist_ok=True)
    return d


def build_one(rid: str, samples: str, live: bool, use_ocr: bool, max_pages: int) -> bool:
    src = None
    for ext in (".pdf", ".docx", ".txt", ".md"):
        cand = os.path.join(samples, rid + ext)
        if os.path.exists(cand):
            src = cand
            break
    if src is None:
        print(f"跳过 {rid}：样本目录里没有这个文件")
        return False

    full_text, sections = P.parse_file(src)
    ocr_info = None

    if use_ocr:
        print(f"  多模态 OCR：逐页读取 {rid} …")
        full_text, sections, ocr_info = run_ocr_enrichment(
            src, full_text, sections, llm_cfg=None, enabled=True, max_pages=max_pages)
        if ocr_info.get("used"):
            print(f"    OCR：{ocr_info['pages']} 页 / {ocr_info['chars']} 字 / "
                  f"{ocr_info['images']} 处图片内容 / 失败 {ocr_info['failed']} 页")
        else:
            print(f"    OCR 未产出：{ocr_info.get('skipped_reason')}")

    rub = default_rubric()
    if live:
        import pipeline as PL
        print(f"  真实模型评阅 {rid}（{len(full_text)} 字）…")
        res = PL.run_grading(full_text, sections, "", report_id=rid,
                             rubric=rub, enable_recheck=True)
        engine = "model"
    else:
        print(f"  离线规则引擎评阅 {rid}（{len(full_text)} 字）…")
        res = run_offline_grading(full_text, sections, rub.items, report_id=rid,
                                  raw_rubric="（内置通用五评分点，见 pipeline.DEFAULT_RUBRIC_ITEMS）")
        engine = "rule"

    if ocr_info:
        if res.run_info is not None:
            apply_ocr_to_runinfo(res.run_info, ocr_info)
        res.ocr = ocr_info

    meta = {
        "engine": engine,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_file": os.path.basename(src),
        "note": ("预置演示结果：由离线规则引擎生成，用于零配置展示完整产品形态；"
                 "每个评分点均已标记待人工复核。"
                 if engine == "rule" else
                 "预置演示结果：由真实模型跑出，与线上同一条流水线。"),
        "full_text": full_text,
        "ocr": ocr_info or {},
        "rubric_name": "通用五评分点（合计 100 分）",
    }
    out = os.path.join(_out_dir(), rid + ".json")
    save_demo_result(out, res, meta)
    n_ev = sum(1 for j in res.judgements if j.evidence)
    print(f"  -> {os.path.relpath(out, ROOT)}  总分 {res.total}  "
          f"带证据 {n_ev}/{len(res.judgements)}  引擎 {engine}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="报告 ID（默认取样本目录下全部）")
    ap.add_argument("--live", action="store_true", help="用真实模型跑（需要 LLM_API_KEY）")
    ap.add_argument("--ocr", action="store_true", help="评阅前先做多模态 OCR（需要支持视觉的模型）")
    ap.add_argument("--max-pages", type=int, default=O.DEFAULT_MAX_PAGES)
    args = ap.parse_args()

    samples = _sample_dir()
    if args.ids:
        ids = args.ids
    else:
        ids = sorted(os.path.splitext(f)[0] for f in os.listdir(samples)
                     if f.lower().endswith((".pdf", ".docx", ".txt", ".md")))
    if not ids:
        print(f"样本目录为空：{samples}")
        return 1

    print(f"样本目录：{os.path.relpath(samples, ROOT)}")
    print(f"输出目录：{os.path.relpath(_out_dir(), ROOT)}")
    print(f"模式：{'真实模型' if args.live else '离线规则引擎'}"
          f"{' + 多模态 OCR' if args.ocr else ''}")
    ok = 0
    for rid in ids:
        if build_one(rid, samples, args.live, args.ocr, args.max_pages):
            ok += 1
    print(f"完成：{ok} 份预置结果已写入 {os.path.relpath(_out_dir(), ROOT)}/")
    print("这份产物会随仓库发布 —— 公开 Demo 打开即可载入，无需 API Key。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
