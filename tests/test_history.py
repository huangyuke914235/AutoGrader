# -*- coding: utf-8 -*-
"""体检历史持久化 —— 这里的 risk 集中在安全，不在功能

三件事必须用测试钉死，因为它们出错时**不会报错，只会默默发生**：

1. 报告正文一旦落盘，就等于把学生的作业留在服务器上还浑然不觉；
2. 报告名来自用户上传的文件名，不消毒就是任意文件写入；
3. 删除接口若不限定根目录，`../../` 能把别处的文件删掉。

所以这里既有「往返一致」的功能测试，也有「不该发生的事真的没发生」的反向断言。
"""
import json
import os
from datetime import datetime

import pytest

import history
import selfcheck as SC

# 只有参数里才该出现的标记串：如果它出现在落盘文件里，说明正文被写进去了
MARKER = "只有这份报告正文里才会出现的独一标记串XYZ"


@pytest.fixture
def hroot(tmp_path):
    return str(tmp_path / "history")


def _result(rid="R1"):
    return SC.run_selfcheck(
        f"实验目的。实验步骤。{MARKER} 数据处理与结果分析。结论。", [],
        experiment_type="", use_ai=False, report_id=rid)


def test_roundtrip_preserves_scores(hroot):
    """存进去再读出来，分数与分项数量不能变。"""
    r = _result()
    history.save_entry(r, "我的报告", history_root=hroot)

    entries = history.load_report("我的报告", history_root=hroot)
    assert len(entries) == 1
    assert entries[0]["data"]["total"] == r.total
    assert len(entries[0]["data"]["items"]) == len(r.items)


def test_saved_file_does_not_contain_the_report_body(hroot):
    """最重要的一条：体检历史只存结论，绝不存作业原文。"""
    history.save_entry(_result(), "R1", history_root=hroot)
    blob = "".join(open(os.path.join(p, f), encoding="utf-8").read()
                   for p, _, fs in os.walk(hroot) for f in fs)
    assert MARKER not in blob, "报告正文被写进了历史文件 —— 这是隐私事故"


def test_saved_file_does_not_contain_api_keys(hroot):
    """Key 从不接触本模块，但要能证明：连疑似形态都搜不到。"""
    history.save_entry(_result("R2"), "R2", history_root=hroot)
    for p, _, fs in os.walk(hroot):
        for f in fs:
            text = open(os.path.join(p, f), encoding="utf-8").read()
            assert "sk-" not in text


@pytest.mark.parametrize("evil", [
    "../../../../etc/passwd",
    "..\\..\\Windows\\System32\\config",
    "C:\\Windows\\win.ini",
    "/tmp/pwned",
])
def test_safe_name_strips_path_separators(evil):
    """目录名来自用户输入：分隔符、盘符一律不能出现在结果里。"""
    s = history.safe_name(evil)
    assert "/" not in s and "\\" not in s and ":" not in s
    assert s != ""


def test_delete_refuses_paths_outside_the_root(hroot):
    """反向对照：拿一个根目录之外的路径来删，必须被拒绝。

    如果这条挂了，说明边界判断根本没生效 —— 那删除按钮就成了任意文件删除。
    """
    outside = os.path.join(hroot, "..", "outside.json")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("x")
    assert history.delete_entry(outside, history_root=hroot) is False
    assert os.path.exists(outside)


def test_delete_distinguishes_io_failure_from_out_of_root(hroot, monkeypatch):
    """反向对照：删不掉 ≠ 越界。

    两种失败都返回 False，但原因完全不同 —— 界面若把磁盘/权限问题
    说成「不在允许的目录范围内」，用户会朝完全错误的方向排查。
    """
    history.save_entry(_result("RD"), "RD", history_root=hroot)
    e = history.load_report("RD", history_root=hroot)[0]

    def _boom(_p):
        raise PermissionError("boom")

    monkeypatch.setattr(history.os, "remove", _boom)
    why = []
    assert history.delete_entry(e["path"], history_root=hroot, reason=why) is False
    assert why and why[0] != "out_of_root", f"IO 失败被误报成越界：{why}"

    why2 = []
    assert history.delete_entry(
        os.path.join(hroot, "..", "x.json"), history_root=hroot,
        reason=why2) is False
    assert why2 == ["out_of_root"], f"越界必须能被单独识别：{why2}"


def test_delete_removes_only_the_target_entry(hroot):
    r = _result("RX")
    history.save_entry(r, "RX", history_root=hroot)
    history.save_entry(r, "RX", history_root=hroot)
    entries = history.load_report("RX", history_root=hroot)
    assert len(entries) == 2
    assert history.delete_entry(entries[0]["path"], history_root=hroot)
    left = history.load_report("RX", history_root=hroot)
    assert len(left) == 1
    assert left[0]["path"] == entries[1]["path"], "删的是第一条，留下来的必须是第二条"


class _FrozenClock:
    """永远返回同一个时刻：逼出「两次保存算出同名文件」的极端情况。"""

    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


def test_two_saves_in_the_same_instant_never_overwrite(hroot, monkeypatch):
    """同一时刻连测两次，必须是两条记录，不能后者覆写前者。

    这是实测踩到的坑：当初时间戳只到秒，同一秒内跑两次体检，
    第二次直接写进了第一次那个文件 —— 趋势图上本该是两个点，
    最后只剩一个，「改完有没有进步」这件事就这么被抹掉了。
    """
    monkeypatch.setattr(history, "datetime",
                        _FrozenClock(datetime(2026, 1, 2, 3, 4, 5, 678000)))
    r1, r2 = _result("RS"), _result("RS")
    r1.total, r2.total = 40.0, 75.0
    history.save_entry(r1, "RS", history_root=hroot)
    history.save_entry(r2, "RS", history_root=hroot)

    assert len(history.load_report("RS", history_root=hroot)) == 2
    assert [v for _, v in history.trend("RS", history_root=hroot)] == [40.0, 75.0]


def test_report_index_keeps_the_display_name_readable(hroot):
    """目录键是消毒过的，界面若直接拿它显示，学生认不出是哪份报告。"""
    history.save_entry(_result("RI"), "我的 实验报告.pdf", history_root=hroot)
    idx = history.report_index(history_root=hroot)
    assert len(idx) == 1
    assert idx[0]["name"] == "我的 实验报告.pdf"
    assert idx[0]["count"] == 1
    assert "/" not in idx[0]["key"] and "\\" not in idx[0]["key"]


def test_saved_entry_can_be_restored_as_a_result_object(hroot):
    """界面要用它算 diff、导出 md，所以落盘的 dict 必须能还原成对象。"""
    r = _result("RO")
    history.save_entry(r, "RO", history_root=hroot)
    obj = history.to_result(history.load_report("RO", history_root=hroot)[0])
    assert obj is not None
    assert obj.total == r.total
    assert len(obj.items) == len(r.items)


def test_to_result_returns_none_on_broken_payload():
    """坏数据返回 None 交调用方跳过，而不是让一条脏记录把历史页面带崩。"""
    assert history.to_result({"data": {"items": "这不是列表"}}) is None


def test_clear_report_empties_everything(hroot):
    history.save_entry(_result("RY"), "RY", history_root=hroot)
    assert history.clear_report("RY", history_root=hroot) == 1
    assert history.load_report("RY", history_root=hroot) == []
    assert "RY" not in history.list_reports(history_root=hroot)


def test_trend_is_chronological(hroot):
    """趋势图的数据源必须按时间正序，画反了会让「进步」看起来像退步。"""
    r1, r2 = _result("RZ"), _result("RZ")
    r1.total, r2.total = 40.0, 75.0
    history.save_entry(r1, "RZ", history_root=hroot)
    history.save_entry(r2, "RZ", history_root=hroot)

    t = history.trend("RZ", history_root=hroot)
    assert [v for _, v in t] == [40.0, 75.0]
    assert history.list_reports(history_root=hroot) == ["RZ"]


def test_trend_keeps_every_point_in_chronological_order(hroot):
    """三次体检就要有三个点，且按时间正序 —— 少一个点或顺序反了，
    「进步」要么看不见，要么被画成退步。"""
    for tot in (30.0, 55.0, 80.0):
        r = _result("R3")
        r.total = tot
        history.save_entry(r, "R3", history_root=hroot)
    t = history.trend("R3", history_root=hroot)
    assert len(t) == 3
    assert [v for _, v in t] == [30.0, 55.0, 80.0]
    assert [k for k, _ in t] == sorted(k for k, _ in t), "横轴必须按时间正序"


def test_corrupted_file_is_skipped_not_fatal(hroot):
    """一条脏数据不该让整个历史页面打不开。"""
    history.save_entry(_result("BAD"), "BAD", history_root=hroot)
    d = os.path.join(hroot, "BAD")
    with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
        f.write("{ 这不是 json")
    entries = history.load_report("BAD", history_root=hroot)
    assert len(entries) == 1, "坏文件应被跳过，而不是崩掉整个列表"
    with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
        json.dump({"result": {}}, f)


# ---------- 公网部署配套：每人一间档案柜 / 档案能带走 ----------

def _entry_with_time(root, name, saved_at, total):
    """直接造一条**指定时间**的记录。

    用 _write_payload 而不是 save_entry：save_entry 写的是「此刻」，
    而这里要证明的恰恰是「导入时时间戳不会被改成导入那一刻」——
    时间必须是可控输入，否则这条断言等于没写。
    """
    r = _result(name)
    r.total = total
    return history._write_payload(root, name, name, "", saved_at,
                                  json.loads(r.model_dump_json()))


def test_two_visitors_never_see_each_others_archives(hroot, monkeypatch):
    """对外开放的头号风险：所有人共用一个目录，A 能看见 B 的档案名和分数。

    反向对照写在同一个断言里：换回甲自己的目录，必须**看得到** ——
    否则「乙看不到」可能只是因为压根没存进去，那就是条假绿灯。
    """
    monkeypatch.setattr(history, "HISTORY_ROOT", hroot)
    a = history.session_root("a" * 16)
    b = history.session_root("b" * 16)
    r = _result("A1")
    r.total = 42.0
    history.save_entry(r, "甲的报告", history_root=a)

    assert history.report_index(a), "甲自己必须看得到自己的档案（反向对照）"
    assert history.report_index(b) == [], "乙绝不能看到甲的档案"
    assert history.load_report("甲的报告", b) == []


def test_session_root_rejects_ids_that_try_to_escape(hroot, monkeypatch):
    """档案空间编号会出现在网址里，用户能改 —— 不校验就是任意路径写入。"""
    monkeypatch.setattr(history, "HISTORY_ROOT", hroot)
    assert history.session_root("0123456789abcdef") == \
        os.path.join(hroot, "0123456789abcdef")
    for bad in ("../../../etc", "a" * 7, "ABCDEF1234567890", "", "12 34", "ab/cd"):
        with pytest.raises(ValueError):
            history.session_root(bad)


def test_export_import_roundtrip_keeps_original_timestamps(hroot):
    """导出再导入，体检的**原始时间**必须原样带回来。

    反向对照就是时间戳本身：若导入时改成「此刻」，9 月测初稿 / 10 月测二稿
    会被压成同一时刻的两条记录，趋势图直接失去意义。
    """
    src, dst = os.path.join(hroot, "src"), os.path.join(hroot, "dst")
    _entry_with_time(src, "初稿", "2026-09-01T10:00:00.000", 40.0)
    _entry_with_time(src, "二稿", "2026-10-01T10:00:00.000", 75.0)

    n, err = history.import_bundle(history.export_bundle(src), dst)
    assert err == ""
    assert n == 2
    a = [(e["saved_at"], e["data"]["total"]) for e in history.load_report("初稿", dst)]
    b = [(e["saved_at"], e["data"]["total"]) for e in history.load_report("二稿", dst)]
    assert a == [("2026-09-01T10:00:00.000", 40.0)]
    assert b == [("2026-10-01T10:00:00.000", 75.0)]
    assert a[0][0][:10] != b[0][0][:10], "两次体检的日期被压成同一天了"


def test_import_rejects_junk_with_a_human_sentence(hroot):
    """导入失败要说人话：学生的出路是「回去重新导出一份」，不是对着 traceback 猜。"""
    for junk in ("{ 这不是 json", '{"nothing": 1}', ""):
        n, err = history.import_bundle(junk, hroot)
        assert n == 0, "垃圾文件不该导入成功"
        assert err and len(err) > 5, "必须给一句人读得懂的原因，不能只返回 0"


def test_sweep_stale_leaves_recent_spaces_alone(hroot, monkeypatch):
    """清扫只能收走长期没人动的档案柜，**绝不能碰还在用的**。

    删除本身在这个沙箱会被拦（SAFE_DELETE_FAIL_CLOSED），
    所以这里守的是更要紧的半边：近期目录一个都不许少。
    """
    monkeypatch.setattr(history, "HISTORY_ROOT", hroot)
    live = history.session_root("c" * 16)
    _entry_with_time(live, "还在用", "2026-09-01T10:00:00.000", 60.0)
    history.sweep_stale(hroot, max_age_days=7)
    assert os.path.isdir(live), "正在使用的档案空间被误删了 —— 这是不可接受的"
