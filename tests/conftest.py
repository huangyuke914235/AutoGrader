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


@pytest.fixture(autouse=True)
def _isolate_upload_dir(tmp_path_factory, monkeypatch):
    """把上传临时目录指向 pytest 的临时目录。

    为什么必须隔离：`test_security` 把 `os.remove` 打桩成 no-op 来验证"确实尝试过删除"，
    于是测试会真的往 `data/tmp_uploads/` 写文件且永不删除 ——
    2026-09-23 检查时那里躺着 37 个 10 字节的 fake-bytes 文件。
    测试不许污染被测试的运行目录。
    """
    d = tmp_path_factory.mktemp("uploads")
    try:
        import app
        monkeypatch.setattr(app, "TMPDIR", str(d), raising=False)
        monkeypatch.setattr(app, "_UPLOAD_CACHE", {}, raising=False)
    except Exception:
        pass
    yield d


def test_upload_dir_is_isolated(_isolate_upload_dir):
    """守住这条隔离：确认真指向 pytest 临时目录，而不是仓库目录"""
    import app
    assert os.path.realpath(app.TMPDIR) == os.path.realpath(str(_isolate_upload_dir))
    assert "tmp_uploads" not in str(app.TMPDIR)
