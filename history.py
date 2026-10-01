# -*- coding: utf-8 -*-
"""体检历史持久化 —— 「初稿 → 修改 → 再看进步」这条主用法不能被刷新打断

为什么非要落盘：健康检查只存 st.session_state 时，浏览器一刷新就全没了，
而学生的典型用法恰恰是跨时间段的 —— 今天测初稿、明天改完再测一次。
「昨天是多少分」这件事不能靠 Web 会话帮忙记住。

纪律（别图省事破坏）：
1. **只存体检结果，绝不存报告原文** —— SelfCheckResult 里没有正文，
   落盘的只有检查项、问题定位与改进方向（issues 里也不含原文引用）。
2. **绝不存 API Key** —— 本模块不接触 llm_cfg。
3. **文件名必须消毒** —— 报告名来自用户的上传文件名，
   不处理 `../` 就等于允许往任意路径写文件。
4. 历史是**学生自己的数据**，界面必须给「删除」。
5. **公网部署时每人一个独立空间**（session_root）：所有人共用一个目录的话，
   A 同学能在档案列表里看到 B 同学的报告名和分数 —— 单机自用没事，
   一对外开放就是隐私事故。参见 app.py 的 _archive_root()。
"""
import json
import os
import re
import shutil
from datetime import datetime

HISTORY_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "history")

_SAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff_\-]+")


def safe_name(name: str, fallback: str = "未命名报告") -> str:
    """把任意用户输入洗成安全的目录/文件名。

    路径分隔符、点号、盘符一律去掉：报告名可能来自上传文件名，
    放任 `../../` 或 `C:\\` 就是任意文件写入。
    """
    cleaned = _SAFE.sub("", (name or "").strip()).strip("-_")
    return cleaned[:60] or fallback


def _stamp(now: datetime) -> str:
    """时间戳精确到毫秒。

    只到秒是不够的：同一秒内连测两次（比如改一个错别字马上重测）
    会算出同一个文件名，后一次**覆写**前一次 —— 不报错、不丢文件，
    但趋势图上这本该是两条记录，最后只剩一条，进步看起来像从没发生过。
    """
    return now.strftime("%Y%m%d-%H%M%S") + f"-{now.microsecond // 1000:03d}"


def _unique_path(day_dir: str, stamp: str) -> str:
    """极端情况（同一毫秒内落两次）再加序号兜底，绝不覆写既有记录。

    后缀用 `_2` 而不是 `-2`，是刻意的：`-`(0x2D) 排在 `.`(0x2E) **前面**，
    于是 `...-678-2.json` 会排到 `...-678.json` 之前；
    两条记录同一毫秒落盘时 `saved_at` 完全相同、排序退化为依赖文件名顺序，
    这个符号差别会让「先 40 后 75」在趋势图上显示成「先 75 后 40」——
    进步被画成退步。`_`(0x5F) 排在 `.` 之后，顺序才是时间序。
    """
    path = os.path.join(day_dir, f"{stamp}.json")
    n = 2
    while os.path.exists(path):
        path = os.path.join(day_dir, f"{stamp}_{n}.json")
        n += 1
    return path


def save_entry(result, name: str, history_root: str = None) -> str:
    """写入一次体检结果，返回文件路径。

    归档目录的键是**用户可见的档案名**，不是 result.report_id ——
    report_id 对上传文件来说是随机生成的一次性会话 id，
    拿它当键的话「今天测的初稿」和「明天改完的二稿」永远落在两个目录里，
    跨会话的进步趋势就废了。档案名由上层显式传入（默认取净化后的文件名）。
    """
    root = history_root or HISTORY_ROOT
    key = safe_name(name or getattr(result, "report_id", ""))
    now = datetime.now()
    return _write_payload(
        root, key, name or key,
        getattr(result, "report_id", "") or "",
        now.isoformat(timespec="milliseconds"),
        json.loads(result.model_dump_json()),
        stamp=_stamp(now))


def _write_payload(root, key, name, report_id, saved_at, result_dict,
                   stamp=None) -> str:
    """落盘的**唯一出口**：save_entry 与 import_bundle 都走这里。

    抽出来不是为了省几行 —— 导入的档案必须带着**原始 saved_at** 落盘。
    若导入时改用「导入那一刻」的时间，一份「9月测初稿、10月测二稿」的档案
    会被压成同一时刻的两条记录，趋势图直接失去意义。
    """
    day = os.path.join(root, safe_name(key))
    os.makedirs(day, exist_ok=True)
    path = _unique_path(day, stamp or _stamp(datetime.now()))
    payload = {"name": name, "report_id": report_id,
               "saved_at": saved_at, "result": result_dict}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def list_reports(history_root: str = None) -> list:
    """列出所有有历史的报告名（按最近写入时间倒序）。"""
    root = history_root or HISTORY_ROOT
    if not os.path.isdir(root):
        return []
    out = []
    for rid in os.listdir(root):
        entries = _entries_of(os.path.join(root, rid))
        if entries:
            out.append((rid, entries[-1]["saved_at"]))
    return [r for r, _ in sorted(out, key=lambda x: x[1], reverse=True)]


def _entries_of(day_dir: str) -> list:
    if not os.path.isdir(day_dir):
        return []
    out = []
    for fn in sorted(os.listdir(day_dir)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(day_dir, fn)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue                      # 坏文件跳过，不能让一条脏数据弄崩整个页面
        out.append({"path": p, "name": d.get("name", fn),
                    "saved_at": d.get("saved_at", fn[:-5]),
                    "data": d.get("result", {})})
    return sorted(out, key=lambda e: e["saved_at"])


def load_report(key: str, history_root: str = None) -> list:
    """读取某份报告的全部历史记录（按时间正序，最早的在前）。"""
    root = history_root or HISTORY_ROOT
    return _entries_of(os.path.join(root, safe_name(key)))


def to_result(entry: dict):
    """把落盘的 dict 还原成 SelfCheckResult，供 diff / 导出复用。

    还原失败返回 None（交给调用方跳过），而不是让一条结构变过的数据
    把整个历史页面带崩 —— 老存档可能来自字段还没定型的版本。
    """
    from models import SelfCheckResult
    try:
        return SelfCheckResult.model_validate(entry.get("data") or {})
    except Exception:
        return None


def delete_entry(path: str, history_root: str = None, reason: list = None) -> bool:
    """删除单条记录；目录空了就顺手清掉。

    路径必须落在 history 根目录内 —— 删出边界是灾难，宁可不删。

    `reason` 是可选的出参：把失败原因分门别类写进去。
    必须区分「越界被拒」（安全问题）和「删不掉」（文件被占用 / 目录只读 /
    沙箱拦截）。两者都返回 False，但对使用者是完全不同的两件事 ——
    把权限问题一律说成「不在允许的目录范围内」，等于让人朝着错误的方向排查。
    """
    root = os.path.abspath(history_root or HISTORY_ROOT)
    abs_p = os.path.abspath(path)
    if os.path.commonpath([root, abs_p]) != root:
        if reason is not None:
            reason.append("out_of_root")
        return False
    try:
        os.remove(abs_p)
    except OSError as e:
        if reason is not None:
            reason.append(str(e))
        return False
    day = os.path.dirname(abs_p)
    try:
        if os.path.isdir(day) and not os.listdir(day):
            os.rmdir(day)
    except OSError:
        pass
    return True


def clear_report(key: str, history_root: str = None) -> int:
    """清空一份报告的全部历史，返回删掉条数。"""
    return sum(1 for e in load_report(key, history_root)
               if delete_entry(e["path"], history_root))


def report_index(history_root: str = None) -> list:
    """所有档案的概览：目录键 / 展示名 / 最近体检时间 / 条数，按最近写入倒序。

    list_reports 只给出目录键（给测试与内部用），界面还需要展示名和条数，
    而目录键是消毒过的（空格、扩展名都被去掉），直接拿给用户看会认不出是哪份报告。
    """
    root = history_root or HISTORY_ROOT
    if not os.path.isdir(root):
        return []
    out = []
    for key in os.listdir(root):
        entries = _entries_of(os.path.join(root, key))
        if not entries:
            continue
        last = entries[-1]
        out.append({"key": key, "name": last["name"] or key,
                    "saved_at": last["saved_at"], "count": len(entries)})
    return sorted(out, key=lambda x: x["saved_at"], reverse=True)


def trend(key: str, history_root: str = None) -> list:
    """某份报告的分数序列，供趋势图使用。

    只取 *总分* 这一条线 —— 分项对比已经有 diff_results 负责，
    趋势要看的是「整体有没有往上走」。
    """
    return [(e["saved_at"][5:16], float(e["data"].get("total") or 0.0))
            for e in load_report(key, history_root)]


# ---------- 公网部署配套：独立空间 / 带走自己的档案 / 定期清扫 ----------

_SID = re.compile(r"^[0-9a-f]{8,64}$")


def session_root(sid: str) -> str:
    """某个访问者的独立档案目录。

    `sid` **必须**是十六进制随机串（由 app.py 生成并校验）：这个值最终会出现在
    网址里，用户能改。放任 `../` 或 `abc/def` 就等于允许往任意目录写文件。
    """
    if not _SID.match(sid or ""):
        raise ValueError("档案空间编号不合法（必须是 8~64 位十六进制）")
    return os.path.join(HISTORY_ROOT, sid)


def export_bundle(history_root: str = None) -> str:
    """把整个档案空间打包成一个 JSON 文本，供学生下载带走。

    为什么必须要有导出：免费云主机的磁盘是**临时**的（应用休眠 / 重新部署即清空），
    而且换浏览器、清了网址就找不到自己那间档案柜了。
    把数据交回给本人，是唯一可靠的持久化方式。
    """
    root = history_root or HISTORY_ROOT
    items = []
    for key in list_reports(root):
        for e in load_report(key, root):
            items.append({"name": e["name"], "saved_at": e["saved_at"],
                          "result": e["data"]})
    return json.dumps({"format": "autograder-history", "version": 1,
                       "exported_at": datetime.now().isoformat(timespec="milliseconds"),
                       "items": items}, ensure_ascii=False, indent=2)


def import_bundle(text: str, history_root: str = None) -> tuple:
    """把导出的 JSON 装回档案空间，返回 (导入条数, 错误说明)。

    错误说明要给**人读得懂的话**：学生拿到的是"文件不对"这种结论，
    正确的出路是"去重新导出一份"，而不是让他对着 traceback 猜。
    """
    root = history_root or HISTORY_ROOT
    try:
        data = json.loads(text or "")
    except ValueError as e:
        return 0, f"这不是合法的 JSON 文件（{e}）"
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return 0, "文件里没有找到体检记录列表，可能不是本工具导出的档案。"
    from models import SelfCheckResult
    ok = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        res = it.get("result")
        try:                       # 结构对不上的记录跳过，绝不让一条脏数据炸掉整次导入
            SelfCheckResult.model_validate(res)
        except Exception:
            continue
        _write_payload(root, it.get("name") or "未命名报告",
                       it.get("name") or "未命名报告",
                       (res or {}).get("report_id", "") or "",
                       it.get("saved_at")
                       or datetime.now().isoformat(timespec="milliseconds"),
                       res)
        ok += 1
    if not ok:
        return 0, "文件里没有一条能识别的体检记录（可能版本不兼容或文件已损坏）。"
    return ok, ""


def sweep_stale(history_root: str = None, max_age_days: int = 7) -> int:
    """清掉长期没人动的档案空间，返回清掉的目录数。

    公网部署时每个访问者一个目录，没人来收就会一直堆在服务器磁盘上。
    只清**顶层**（档案空间）这一层，且不碰任何非目录文件。
    删除失败（只读磁盘 / 沙箱拦截）一律吞掉 —— 清扫是锦上添花，
    不能因为它把应用启动给弄崩。
    """
    root = history_root or HISTORY_ROOT
    if not os.path.isdir(root):
        return 0
    cutoff = datetime.now().timestamp() - max_age_days * 86400
    n = 0
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        try:
            mtime = os.path.getmtime(d)
            for f in os.listdir(d):
                mtime = max(mtime, os.path.getmtime(os.path.join(d, f)))
        except OSError:
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(d)
                n += 1
            except OSError:
                pass
    return n
