# -*- coding: utf-8 -*-
"""D2 生死线 · 最小链路稳定性测试

用法：
    source .venv/Scripts/activate
    python tools/stability_test.py            # 默认 10 次
    python tools/stability_test.py 20         # 跑 20 次

判定标准（写在 02 技术执行步骤里，不许放宽）：
    同一输入连跑 N 次 -> 合法 JSON 率 >= 90%，且 quote 原文精确匹配率 >= 90%
"""
import sys
import os
import time
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as P
import prompts
from models import RubricItem, ItemJudgement
from llm import call_json, verify_evidence, get_env, get_stats

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "samples")

# 手写的评分点（故意选一个有明确原文依据的，方便检验 quote 能否匹配）
TEST_ITEM = RubricItem(
    id="r1",
    name="实验步骤完整性",
    criteria="是否完整描述了实验的操作步骤或实现过程，而不是只贴最终结果或代码",
    max_score=20,
    positive_signals=["步骤", "实现", "过程", "设计", "流程"],
    negative_signals=["只有结果", "无过程说明"],
)


def load(sample="S02.txt"):
    path = os.path.join(SAMPLES_DIR, sample)
    full = open(path, encoding="utf-8").read()
    return full, P.split_sections(full)


def build_user(item, sections, full_text):
    picked = P.retrieve(sections, item.positive_signals, top_k=4)
    ctx = "\n\n".join(f"[{s.id}] {s.title}\n{s.text[:2500]}" for s in picked)
    return (f"评分点：{item.name}\n判定标准：{item.criteria}\n满分：{item.max_score}\n"
            f"命中特征：{'、'.join(item.positive_signals)}\n\n"
            f"报告相关片段：\n{ctx}")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    full, sections = load()
    print(f"样本：S02.txt  {len(full)} 字，切出 {len(sections)} 个章节")
    print(f"模型：{get_env('LLM_MODEL')}")
    print(f"将对同一个评分点判定 {n} 次\n" + "=" * 62)

    user = build_user(TEST_ITEM, sections, full)
    ok_json = quote_ok = retry_ok = 0
    verdicts = collections.Counter()
    confs, times, fails = [], [], []
    samples_out = []

    for i in range(1, n + 1):
        t0 = time.time()
        try:
            j = call_json(prompts.S2_JUDGE, user, ItemJudgement)
            ok_json += 1
            verdicts[j.verdict] += 1
            confs.append(j.confidence)
            hit = verify_evidence(j, full)
            if hit:
                quote_ok += 1
            else:
                # 触发一次驳回重跑（这是 pipeline 里的真实逻辑）
                ru = user + ("\n\n【上一次判定被驳回】quote 无法在原文中逐字匹配，"
                             "必须原样复制片段原句；找不到依据就判 miss。")
                try:
                    j2 = call_json(prompts.S2_JUDGE, ru, ItemJudgement)
                    if verify_evidence(j2, full):
                        retry_ok += 1
                except Exception:
                    pass
            el = time.time() - t0
            times.append(el)
            top = (j.evidence[0].quote[:26] if j.evidence else "")
            print(f"  {i:>2}/{n}  {j.verdict:<8} {j.score:>5.1f}分  置信{j.confidence:.2f}  "
                  f"证据{'✓' if hit else '✗'}  {el:>5.1f}s  「{top}」")
            if i <= 3:
                samples_out.append({"verdict": j.verdict, "score": j.score,
                                    "reason": j.reason,
                                    "evidence": [e.quote for e in j.evidence]})
        except Exception as e:
            fails.append(str(e)[:120])
            print(f"  {i:>2}/{n}  调用失败：{str(e)[:90]}")

    print("=" * 62)
    nj = max(ok_json, 1)
    print(f"合法 JSON 率      ：{ok_json}/{n} = {ok_json/n*100:.0f}%   （标准 >= 90%）")
    print(f"证据首次匹配率    ：{quote_ok}/{nj} = {quote_ok/nj*100:.0f}%   （标准 >= 90%）")
    print(f"驳回重跑后救回    ：{retry_ok} 次 -> 最终有效证据率 "
          f"{(quote_ok+retry_ok)/nj*100:.0f}%")
    if confs:
        print(f"置信度均值        ：{sum(confs)/len(confs):.2f}")
    if times:
        print(f"单次耗时          ：均值 {sum(times)/len(times):.1f}s，合计 {sum(times):.0f}s")
    print(f"判定分布          ：{dict(verdicts)}")
    if fails:
        print(f"失败 {len(fails)} 次：{fails[:2]}")

    st = get_stats()
    print(f"Token 消耗        ：{st['tokens']}")
    print("=" * 62)
    jr = (quote_ok + retry_ok) / nj * 100
    verdict = "通过 ✅ 继续 AutoGrader" if (ok_json / n >= 0.9 and jr >= 0.9) \
        else "未达标 ⚠ 按纪律应于明早更换备选选题"
    print(f"生死线判定：{verdict}")

    # 留痕
    os.makedirs("docs", exist_ok=True)
    log = {
        "date": time.strftime("%Y-%m-%d %H:%M"), "runs": n,
        "valid_json_rate": round(ok_json / n, 3),
        "quote_match_rate": round(quote_ok / nj, 3),
        "rescued_by_retry": retry_ok,
        "final_evidence_rate": round(jr / 100, 3),
        "verdicts": dict(verdicts), "tokens": st["tokens"],
        "samples": samples_out,
    }
    with open("docs/stability_log.json", "w", encoding="utf-8") as f:
        import json
        json.dump(log, f, ensure_ascii=False, indent=2)
    print("结果已写入 docs/stability_log.json（作为开发过程留痕）")


if __name__ == "__main__":
    main()
