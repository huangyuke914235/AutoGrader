import sys, os, json, time
sys.path.insert(0,'.')
import parser as P
from pipeline import run_grading, stage_rubric

full, secs = P.parse_file('data/samples/S02.txt')
print(f'样本 {len(full)}字 / {len(secs)}章节')
rub = stage_rubric("实验目的明确；实验步骤完整可复现；有实验结果截图或数据；有结果分析；代码规范有注释；有实验总结与心得。总分100", "Java程序设计 课程实验")
print('原子化评分点：')
for i in rub.items: print(f'  {i.id} {i.name} {i.max_score}分 - {i.criteria[:34]}')
print('总分校验:', round(sum(i.max_score for i in rub.items),1))
t=time.time()
res = run_grading(full, secs, "", report_id="S02", rubric=rub, enable_recheck=True)
print(f'\n=== 总分 {res.total} （代码加总，耗时 {res.elapsed_sec}s）===')
for it in res.items:
    j=[x for x in res.judgements if x.rubric_item_id==it.id][0]
    print(f'  {j.verdict:<8}{j.score:>5.1f}/{it.max_score:<5} 置信{j.confidence:.2f} {"[待复核]" if j.needs_review else ""} {it.name}')
    print(f'      理由: {j.reason[:60]}')
    for e in j.evidence[:1]: print(f'      证据: 「{e.quote[:40]}」 @{e.section_id}')
print('\n评语:', res.feedback.summary[:120] if res.feedback else '无')
for s in (res.feedback.suggestions if res.feedback else [])[:3]: print('  -', s[:70])
