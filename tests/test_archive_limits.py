# -*- coding: utf-8 -*-
"""档案空间的磁盘上限（公开部署加固）

要防的场景：`?sid=<16位十六进制>` 是**用户可以随手改的**，每换一个编号就会在
服务器磁盘上多一间目录。原有的"7 天清扫"限制的是**留存时长**，
限制不了**同时存在多少间** —— 循环请求就能把磁盘写满，
而磁盘满是这类免费云主机最常见的封禁原因。

所以需要把"无界"变成"有界"，并且这一层必须由代码拦住，
不能指望"用户不会那么干"。
"""
import os

import history as HIST


def _touch_space(root, sid):
    d = os.path.join(root, sid)
    os.makedirs(d, exist_ok=True)
    return d


# ---------- 计数与存在性 ----------

def test_count_spaces_counts_only_directories(tmp_path):
    root = str(tmp_path)
    _touch_space(root, "a" * 16)
    _touch_space(root, "b" * 16)
    (tmp_path / "loose_file.json").write_text("{}", encoding="utf-8")
    assert HIST.count_spaces(root) == 2, "只数目录，散落的文件不算一间档案空间"


def test_count_spaces_on_missing_root_is_zero(tmp_path):
    assert HIST.count_spaces(str(tmp_path / "nope")) == 0


def test_space_exists_distinguishes_old_visitor_from_new_id(tmp_path):
    root = str(tmp_path)
    _touch_space(root, "c" * 16)
    assert HIST.space_exists("c" * 16, root) is True
    assert HIST.space_exists("d" * 16, root) is False


def test_space_exists_rejects_malformed_id(tmp_path):
    """非法编号不能让判断炸掉，也不能被当成"已存在"而放进写入路径"""
    root = str(tmp_path)
    for bad in ("", "../../etc", "abc/def", "ABCDEF1234567890", "zz" * 8):
        assert HIST.space_exists(bad, root) is False


# ---------- 闸门 ----------

def test_can_open_new_space_respects_limit(tmp_path):
    root = str(tmp_path)
    assert HIST.can_open_new_space(root, limit=3) is True
    for i in range(3):
        _touch_space(root, f"{i:016x}")
    assert HIST.can_open_new_space(root, limit=3) is False, "达到上限后必须拒绝再开新空间"
    assert HIST.count_spaces(root) == 3


def test_limit_default_is_generous_enough_for_a_class():
    """上限不能小到影响正常使用：一个班 + 若干访客要放得下"""
    assert HIST.MAX_ARCHIVE_SPACES >= 100


def test_session_root_honours_custom_history_root(tmp_path):
    """能指定根目录，测试与"退回临时目录"两条路径都依赖这个参数"""
    custom = str(tmp_path / "elsewhere")
    p = HIST.session_root("e" * 16, history_root=custom)
    assert p.startswith(custom)
    assert os.path.basename(p) == "e" * 16


def test_session_root_still_rejects_traversal_with_custom_root(tmp_path):
    """加了参数也不能放松校验 —— 这是防任意路径写入的唯一一道闸"""
    import pytest
    for bad in ("../../etc", "abc/def", "", "ZZZZZZZZ"):
        with pytest.raises(ValueError):
            HIST.session_root(bad, history_root=str(tmp_path))
