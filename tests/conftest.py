import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 测试绝不能用到真实 API key：把它清空，防止任何测试误触发真实调用
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"     # 指向不可达地址
os.environ["LLM_MODEL"] = "fake-model-for-tests"


@pytest.fixture(autouse=True)
def _no_real_calls(monkeypatch):
    """任何测试都不允许真的发出网络请求"""
    import llm

    def boom(*a, **k):
        raise AssertionError("测试里不允许调用真实模型（请 mock pipeline.call_json）")

    monkeypatch.setattr(llm, "_get_client", boom)
    yield
