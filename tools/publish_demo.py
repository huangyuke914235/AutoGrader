# -*- coding: utf-8 -*-
"""把已有的预置结果发布成主页案例（**不调用模型**）

    python tools/publish_demo.py            # 用 data/demo/ 里已有的结果
    python tools/publish_demo.py --first S03

为什么不重新跑一遍：主页案例必须和 Demo 里载入的那一份**是同一次评阅的结果**。
重跑会产生一次新的结果（模型有波动），于是网页上的分数和 Demo 里的分数对不上 ——
评委正好会拿这两处对照。所以这里只做脱敏与裁剪，不产生任何新的判定。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline as PL
import tools.build_demo as BD


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", help="主页默认展示的案例 ID（排在案例列表第一位）")
    args = ap.parse_args()

    demos = PL.demo_candidates()
    if not demos:
        print("找不到预置结果：先跑 python tools/build_demo.py --live")
        return 1

    ids = [os.path.splitext(os.path.basename(p))[0] for p in demos]
    default_id = ""
    if args.first:
        if args.first not in ids:
            print(f"--first {args.first} 不在预置结果里（现有：{ids}）")
            return 1
        default_id = args.first

    published = []
    for order, rid in enumerate(ids):
        path = next(p for p in demos
                    if os.path.splitext(os.path.basename(p))[0] == rid)
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        meta = payload.get("_meta") or {}
        full_text = meta.get("full_text", "")
        from models import GradingResult
        res = GradingResult.model_validate(payload["result"])

        out = BD.publish_case(rid, res, full_text, order)
        published.append({"id": rid, "order": order})
        print(f"  {rid}: {res.total} 分 / {meta.get('engine')} / "
              f"{sum(len(j.evidence) for j in res.judgements)} 条证据 "
              f"-> {os.path.relpath(out, BD.ROOT)}")

    m = BD.write_manifest(published, default_id)
    print(f"\n主页案例列表已更新：{os.path.relpath(m, BD.ROOT)}")
    print(f"  cases（字母序）: {sorted(e['id'] for e in published)}")
    print(f"  默认展示: {default_id or '（未指定，主页用自己的默认值）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
