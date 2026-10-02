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
import json
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


def existing_engine(rid: str) -> str:
    """已存在的预置结果是用什么引擎产出的（没有则返回空串）"""
    p = os.path.join(_out_dir(), rid + ".json")
    if not os.path.exists(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8") as f:
            return ((json.load(f).get("_meta") or {}).get("engine") or "")
    except Exception:
        return ""


def build_one(rid: str, samples: str, live: bool, use_ocr: bool, max_pages: int,
              force: bool = False) -> bool:
    src = None
    for ext in (".pdf", ".docx", ".txt", ".md"):
        cand = os.path.join(samples, rid + ext)
        if os.path.exists(cand):
            src = cand
            break
    if src is None:
        print(f"跳过 {rid}：样本目录里没有这个文件")
        return False

    # 防止「用便宜引擎覆盖贵引擎」：真实模型结果是有成本换来的，
    # 而且**对外公布的分数就是它**。离线规则引擎重跑一次就会把它悄悄换掉，
    # 主页案例、PPT、README 里的数字随之全部对不上 —— 这个坑本文件已经踩过一次
    # （用 --no-publish 做验证时把三份真实模型结果覆盖成了规则引擎产物）。
    want = "model" if live else "rule"
    have = existing_engine(rid)
    if have and have != want and not force:
        print(f"跳过 {rid}：现有结果是「{have}」引擎产物，本次要用「{want}」——"
              f"覆盖会让对外数字变化。确认要覆盖请加 --force")
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
    return res, full_text


def publish_case(rid: str, res, full_text: str, order: int) -> str:
    """把预置结果同时发布成**主页可看的脱敏案例**（docs/cases/<id>.json）

    为什么从同一份数据出两个产物（`data/demo/` 给应用、`docs/cases/` 给主页）：
    两边各自构建会出现「网页上讲的结果和 Demo 里跑的不是同一次」——
    评委正好会拿这两处对照，对不上就是硬伤。

    脱敏规则直接复用 `publish_cases.build_public_text` / `check_public`，不重写一份：
    正文只保留「每条证据 ±500 字」的窗口，且必须通过学号/手机/邮箱扫描才允许写出。
    """
    from publish_cases import build_public_text, check_public, CASES

    data = {
        "report_id": res.report_id,
        "filename": rid + ".txt",
        "total_score": res.total,
        "full_text": full_text,
        "items": [{"id": it.id, "name": it.name, "max_score": it.max_score}
                  for it in res.items],
        "judgements": [json.loads(j.model_dump_json()) for j in res.judgements],
        "feedback": (json.loads(res.feedback.model_dump_json())
                     if res.feedback else {"summary": "", "suggestions": []}),
        "engine": res.model,
        "_order": order,
    }
    data["full_text"] = build_public_text(full_text, data["judgements"])
    data["_is_public"] = True
    data["_note"] = "正文已裁剪为证据片段窗口，仅用于公开演示"

    problems = check_public(data)
    if problems:
        # 宁可失败，也不把不合规的内容写进公开目录
        raise SystemExit(f"{rid} 未通过公开自检：{problems}")

    os.makedirs(CASES, exist_ok=True)
    out = os.path.join(CASES, rid + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return out


def write_manifest(entries, default_id: str = ""):
    """主页案例列表的唯一来源。

    两条约束必须同时满足，别为了"让某个案例先出现"去动列表顺序：
    - `cases` **按字母序**：`tests/test_security.py` 用它校验列表与文件一致；
    - 默认展示哪一个由 `default` 字段表达，主页读它（见 docs/index.html）。
    """
    from publish_cases import MANIFEST
    ids = sorted(e["id"] for e in entries)
    payload = {
        "cases": ids,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    if default_id and default_id in ids:
        payload["default"] = default_id
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return MANIFEST


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="报告 ID（默认取样本目录下全部）")
    ap.add_argument("--live", action="store_true", help="用真实模型跑（需要 LLM_API_KEY）")
    ap.add_argument("--ocr", action="store_true", help="评阅前先做多模态 OCR（需要支持视觉的模型）")
    ap.add_argument("--max-pages", type=int, default=O.DEFAULT_MAX_PAGES)
    ap.add_argument("--first", help="指定主页默认展示的案例 ID（放在案例列表第一位）")
    ap.add_argument("--no-publish", action="store_true",
                    help="只生成应用用的预置结果，不更新主页案例（docs/cases/）")
    ap.add_argument("--force", action="store_true",
                    help="允许用不同的引擎覆盖已有预置结果（会把对外公布的数字改掉）")
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

    # 主页默认案例排到第一位：评委打开主页看到的那一份，应当是证据链最完整的
    if args.first and args.first in ids:
        ids = [args.first] + [i for i in ids if i != args.first]
    elif args.first:
        print(f"提示：--first {args.first} 不在样本列表里，忽略")

    print(f"样本目录：{os.path.relpath(samples, ROOT)}")
    print(f"输出目录：{os.path.relpath(_out_dir(), ROOT)}")
    print(f"模式：{'真实模型' if args.live else '离线规则引擎'}"
          f"{' + 多模态 OCR' if args.ocr else ''}")
    ok, published = 0, []
    for order, rid in enumerate(ids):
        r = build_one(rid, samples, args.live, args.ocr, args.max_pages,
                      force=args.force)
        if not r:
            continue
        ok += 1
        res, full_text = r
        if not args.no_publish:
            out = publish_case(rid, res, full_text, order)
            published.append({"id": rid, "order": order})
            print(f"     -> 主页案例 {os.path.relpath(out, ROOT)}"
                  f"（{res.total} 分，已裁剪脱敏）")
    print(f"完成：{ok} 份预置结果已写入 {os.path.relpath(_out_dir(), ROOT)}/")
    if published:
        m = write_manifest(published)
        order_ids = [e["id"] for e in sorted(published, key=lambda x: x["order"])]
        print(f"主页案例列表已更新：{os.path.relpath(m, ROOT)}　顺序 {order_ids}")
    print("这份产物会随仓库发布 —— 公开 Demo 打开即可载入，无需 API Key。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
