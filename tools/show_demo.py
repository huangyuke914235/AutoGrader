# -*- coding: utf-8 -*-
"""看一眼预置演示结果的内容（人工核对用）：python tools/show_demo.py [S03]"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rid = sys.argv[1] if len(sys.argv) > 1 else "S03"
path = os.path.join(ROOT, "data", "demo", rid + ".json")
d = json.load(open(path, encoding="utf-8"))
meta, r = d["_meta"], d["result"]

print(f"报告 {r['report_id']}  引擎={meta.get('engine')}  模型={r.get('model')}  "
      f"用时={r.get('elapsed_sec')}s  总分={r['total']}")
ri = r.get("run_info") or {}
print(f"调用 {ri.get('calls')} 次 / token {ri.get('tokens')} / 失败 {ri.get('failed_calls')} / "
      f"JSON 修复 {ri.get('json_repaired')} / 解析覆盖 {ri.get('parse_coverage')}")
print("-" * 70)
for it in r["items"]:
    j = next((x for x in r["judgements"] if x["rubric_item_id"] == it["id"]), None)
    if not j:
        continue
    print(f"{it['name']}  {j['verdict']}  {j['score']}/{it['max_score']}  "
          f"置信 {j['confidence']}  待复核 {j['needs_review']}")
    print(f"  理由：{j['reason'][:160]}")
    for e in j["evidence"][:2]:
        print(f"  · 证据：{e['quote'][:80]}")
print("-" * 70)
fb = r.get("feedback") or {}
print("总评：", (fb.get("summary") or "")[:300])
print("建议：")
for s in (fb.get("suggestions") or [])[:4]:
    print("  -", s[:140])
