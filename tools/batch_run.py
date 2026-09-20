import sys,os,json,time
sys.path.insert(0,'.')
import parser as P
import json as J
from pipeline import run_grading
from models import Rubric, RubricItem

items=[RubricItem(id="r1",name="实验目的明确",criteria="开头明确写出实验目的",max_score=15,positive_signals=["实验目的","旨在","掌握"]),
RubricItem(id="r2",name="实验环境与步骤",criteria="写清环境配置与操作步骤",max_score=20,positive_signals=["环境","步骤","安装","配置"]),
RubricItem(id="r3",name="核心代码与实现",criteria="给出核心代码或关键实现说明",max_score=25,positive_signals=["代码","程序","实现","class","public"]),
RubricItem(id="r4",name="运行结果与数据",criteria="给出运行结果、截图或数据",max_score=20,positive_signals=["运行结果","输出","结果","截图"]),
RubricItem(id="r5",name="分析与总结",criteria="对结果进行分析并有总结心得",max_score=20,positive_signals=["分析","总结","心得","讨论"])]
rub=Rubric(items=items)
meta=J.load(open('data/samples/meta.json',encoding='utf-8'))
out=[]
for m in meta:
    rid=m['report_id']
    if m['chars']>20000: 
        print(f"{rid} 跳过（{m['chars']}字，超长）"); continue
    full,secs=P.parse_file(f'data/samples/{rid}.txt')
    try:
        res=run_grading(full,secs,"",report_id=rid,rubric=rub,enable_recheck=False)
        line=f"{rid}  {res.total:>5.1f}分  {res.elapsed_sec:>5.1f}s  {m['original'][:30]}"
        print(line); out.append({"report_id":rid,"total":res.total,"name":m['original'],"chars":m['chars'],
            "details":[{ "id":j.rubric_item_id,"v":j.verdict,"s":j.score} for j in res.judgements]})
    except Exception as e:
        print(f"{rid} 失败: {str(e)[:80]}")
tot=[o['total'] for o in out]
print("\n=== 汇总 ===")
if tot: print(f"分数分布: {sorted(tot)}  最高{max(tot)} 最低{min(tot)} 极差{max(tot)-min(tot)}")
print("能否区分好坏:", "能 ✅" if tot and max(tot)-min(tot)>=15 else "不能 ⚠ 判定过于一律")
J.dump(out,open('data/results/batch.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)
