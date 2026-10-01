# -*- coding: utf-8 -*-
"""模型输出 JSON 的宽容解析测试（只修格式，不改内容）"""
import json

import pytest

from llm import loads_json, escape_inner_quotes, close_truncated, salvage_truncated

CASES = [
    ("本来就合法", '{"summary":"写得不错","suggestions":["a","b"]}'),
    ("summary 里嵌裸引号",
     '{"summary":"报告里用"运行结果示例"当小标题","per_item":{},"suggestions":["补图"]}'),
    ("数组里嵌裸引号（线上真实报错形态）",
     '{"summary":"尚可","per_item":{},"suggestions":["把"运行结果"贴进去","补版本号"]}'),
    ("字符串内有未转义换行",
     '{"summary":"第一段\n第二段","suggestions":["a"]}'),
    ("被 max_tokens 截断",
     '{"summary":"结构完整但结果缺失","per_item":{"r1":"好"},"suggestions":["补图","补总'),
    ("代码围栏包裹", '```json\n{"summary":"ok","suggestions":["a"]}\n```'),
    # 下面这些连「补齐括号」都救不回来，只能靠渐进截断抢救
    ("数组未闭合且字符串破损",
     '{"summary":"评价","per_item":{},"suggestions":["第一条","第二条}'),
    ("中间少了一个引号",
     '{"summary":"评价","per_item":{"r1:好"},"suggestions":["a"]}'),
]


@pytest.mark.parametrize("name,raw", CASES)
def test_all_broken_forms_are_parsed(name, raw):
    data = loads_json(raw)
    assert isinstance(data, dict), f"{name} 未解析成功"
    assert "summary" in data


def test_content_is_preserved_not_rewritten():
    """转义只是让字符合法化，内容本身不许被改"""
    raw = '{"summary":"他说"运行结果示例"很清楚","suggestions":["补图"]}'
    data = loads_json(raw)
    assert "运行结果示例" in data["summary"]
    assert '"' in data["summary"], "半角引号应当被保留，而不是删掉"


def test_valid_json_does_not_go_through_repair():
    """合法输入不应被改写——先严格解析，只有失败才降级"""
    raw = '{"summary":"正常文本","suggestions":["a"]}'
    assert loads_json(raw) == json.loads(raw)


def test_salvage_returns_none_when_hopeless():
    assert salvage_truncated("") is None
    assert salvage_truncated("{") is None


def test_close_truncated_balances_brackets():
    assert close_truncated('{"a":["b"') == '{"a":["b"]}'
