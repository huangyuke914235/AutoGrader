# -*- coding: utf-8 -*-
"""导入冒烟：防止「用了但忘了 import」这类低级错误溜进主流程

（2026-09-23 就在 tools/batch_run.py 上栽过一次：main() 里用了 argparse 却没导入。）
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODULES = ["models", "llm", "parser", "prompts", "pipeline", "batch_ui"]
# 注意：test_key.py 是「导入即运行」的脚本（会读 .env 并连模型），不能在这里 import，
# 它的语法正确性由 compileall 与下面的静态检查保证。
TOOLS = ["batch_run", "benchmark", "import_gold", "anonymize", "make_demo",
         "publish_cases"]


@pytest.mark.parametrize("name", MODULES)
def test_core_module_imports(name):
    importlib.import_module(name)


@pytest.mark.parametrize("name", TOOLS)
def test_tool_module_imports(name):
    importlib.import_module(f"tools.{name}")


def test_tool_mains_do_not_reference_undefined_names():
    """静态检查：每个 tools 脚本里用到的顶层名字，要么已导入、要么是本模块定义"""
    import ast
    for name in TOOLS:
        path = os.path.join(ROOT, "tools", f"{name}.py")
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.asname or a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(a.asname or a.name for a in node.names)
        defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        defined |= {t.id for n in tree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        # 收集所有被读取的名字，减去本模块定义/导入/内置，剩下的可疑项里
        # 只报警那些明显是标准库模块名的（argparse / datetime / json ...）
        loaded = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
                  and isinstance(n.ctx, ast.Load)}
        stdlib_hint = {"argparse", "datetime", "json", "os", "sys", "re", "math",
                       "time", "glob", "statistics", "collections", "hashlib"}
        suspicious = (loaded - defined - imported - set(dir(__builtins__))) & stdlib_hint
        assert not suspicious, f"tools/{name}.py 疑似使用了未导入的模块：{suspicious}"
