# -*- coding: utf-8 -*-
"""公网部署的三道闸 —— 用 AppTest 在进程内跑真实 Streamlit 运行时验证。

为什么要有这个脚本：这三条一旦坏掉**不会报错**，只会默默发生 ——
「A 同学看到 B 同学的分数」在开发机上永远看不出来，等放到公网就成事故。
单测只能验 history 这一层，验不到「app 有没有真的把隔离用上」。

    python tools/smoke_public_deploy.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("DEMO_MODE", "true")
os.environ.pop("LLM_API_KEY", None)

from streamlit.testing.v1 import AppTest       # noqa: E402

OK, BAD = 0, 0


def check(name, cond, extra=""):
    global OK, BAD
    if cond:
        OK += 1
        print(f"  ✓ {name}")
    else:
        BAD += 1
        print(f"  ✗ {name} {extra}")


def boot(**qp):
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    for k, v in qp.items():
        at.query_params[k] = v
    at.run()
    return at


print("① 档案空间按人隔离：两个人不能互相看见")
at_a = boot(sid="a" * 16)
at_b = boot(sid="b" * 16)
import history as HIST                          # noqa: E402

root_a = HIST.session_root("a" * 16)
root_b = HIST.session_root("b" * 16)
r = HIST.to_result(HIST.load_report("X", root_a)[0]) if HIST.load_report("X", root_a) else None
# 造一条属于甲的记录，再看乙能不能看见
from selfcheck import run_selfcheck             # noqa: E402

res = run_selfcheck("实验目的。实验步骤。数据处理与结果分析。结论。", [],
                    experiment_type="", use_ai=False, report_id="SMOKE-ISOLATION")
HIST.save_entry(res, "甲的隐私报告", history_root=root_a)
check("甲看得到自己的档案", bool(HIST.report_index(root_a)))
check("乙看不到甲的档案", HIST.report_index(root_b) == [],
      f"实际：{HIST.report_index(root_b)}")

print("② 网址里的编号是**不可信输入**：乱填不能崩、也不能越权")
for junk in ("../../../etc", "abc", "12 34", "'; DROP TABLE", "%2e%2e%2f"):
    try:
        atj = boot(sid=junk)
        check(f"乱填编号「{junk}」页面不崩", len(atj.exception) == 0,
              f"异常：{[e.value for e in atj.exception][:1]}")
    except Exception as e:
        check(f"乱填编号「{junk}」页面不崩", False, f"{type(e).__name__}: {e}")

print("③ 公网部署不把评阅结果写进服务器磁盘（结果含原文引用）")
import importlib                                # noqa: E402
import app as APP                               # noqa: E402

importlib.reload(APP)
os.environ["AG_PUBLIC"] = "1"
check("AG_PUBLIC=1 时判定为公网", APP._public_host() is True)
os.environ["AG_PUBLIC"] = "0"
check("AG_PUBLIC=0 时判定为本地", APP._public_host() is False)
os.environ.pop("AG_PUBLIC", None)

APP._public_host.cache_clear() if hasattr(APP._public_host, "cache_clear") else None
os.environ["AG_PUBLIC"] = "1"
from models import GradingResult                # noqa: E402

try:
    fake = GradingResult(report_id="SMOKE-PUB", items=[], rubrics=[], total=0.0)
    p = APP.save_result(fake, "SMOKE-PUB")
    check("公网模式下 save_result 不落盘", p == "", f"实际写到了 {p}")
except Exception as e:
    check("公网模式下 save_result 不落盘", False, f"{type(e).__name__}: {e}")
os.environ.pop("AG_PUBLIC", None)

print("④ 档案导出后能原样导入回来（换设备/服务器清空时的唯一退路）")
bundle = HIST.export_bundle(root_a)
n, err = HIST.import_bundle(bundle, root_b)
check("导出→导入条数对得上", n >= 1 and err == "", f"n={n} err={err}")
check("导入后乙的空间里有了这条记录", bool(HIST.report_index(root_b)))

print("⑤ 清理本次冒烟造的数据（清不掉也只是留个空目录，不谎报）")
for d in (root_a, root_b):
    try:
        HIST.clear_report("甲的隐私报告", d)
    except Exception:
        pass

print()
print(f"SMOKE {'OK' if BAD == 0 else 'FAILED'}  ({OK} 项通过 / {BAD} 项失败)")
sys.exit(1 if BAD else 0)
