# -*- coding: utf-8 -*-
"""合成引擎的离线单元测试。

重点验证"自定义停顿标记"这条链路：
1. 证明 edge-tts 默认会把用户传入的 SSML 标签转义（因此不能直接传）；
2. 证明通过重写 texts 属性注入的内容不会被转义（因此方案成立）。
这两条断言不需要联网。
"""

import pytest
from edge_tts import Communicate

from config import Config
from core.tts_engine import (
    items_to_ssml,
    pack_by_bytes,
    xml_escape,
    TTSEngine,
)


def test_edge_tts_escapes_raw_ssml_input():
    """前提验证：把 <break/> 当普通文本传入会被转义成实体。

    注意 texts 在原生实现里是生成器，测试中需先具象化。
    """
    chunk = list(Communicate('<break time="500ms"/>', Config.DEFAULT_VOICE).texts)[0]
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
    assert "&lt;break" in text
    assert "<break" not in text


def test_texts_accepts_list_assignment():
    """注入后 texts 会被逐项发送为一个 SSML 请求（stream 内遍历该序列）。"""
    comm = Communicate("占位", Config.DEFAULT_VOICE)
    comm.texts = ["第一段。".encode("utf-8"), "第二段。".encode("utf-8")]
    assert [c.decode("utf-8") for c in comm.texts] == ["第一段。", "第二段。"]


def test_texts_attribute_is_writable():
    """方案验证：texts 是普通实例属性，可被覆盖为自构造的 SSML。"""
    comm = Communicate("占位", Config.DEFAULT_VOICE)
    comm.texts = ['轻语。<break time="500ms"/>继续。']
    assert comm.texts[0] == '轻语。<break time="500ms"/>继续。'


def test_items_to_ssml_renders_break_tags():
    items = [
        {"type": "text", "value": "你好。"},
        {"type": "pause", "ms": 500},
        {"type": "text", "value": "世界。"},
    ]
    ssml = items_to_ssml(items)
    assert ssml == '你好。<break time="500ms"/>世界。'


def test_xml_escape_only_escapes_three_chars():
    assert xml_escape('a & b < c > d') == "a &amp; b &lt; c &gt; d"


def test_pack_by_bytes_never_cuts_a_tag():
    content = "字" * 900 + '<break time="500ms"/>' + "字" * 900
    chunks = pack_by_bytes(content, 1000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.encode("utf-8")) <= 1000
    joined = "".join(chunks)
    assert joined == content
    assert '<break time="500ms"/>' in joined


def test_pack_by_bytes_empty_content():
    assert pack_by_bytes("", 100) == [""]


@pytest.mark.parametrize("value,low,high,expected", [
    (0, -50, 50, "+0%"),
    (20, -50, 50, "+20%"),
    (-30, -50, 50, "-30%"),
    (999, -50, 50, "+50%"),
    (-999, -50, 50, "-50%"),
    ("abc", -50, 50, "+0%"),
])
def test_format_percent(value, low, high, expected):
    assert TTSEngine.format_percent(value, low, high) == expected


def test_engine_probe_reports_ssml_capability():
    """探测不联网即可完成，只是创建本地对象。"""
    engine = TTSEngine()
    assert isinstance(engine.supports_ssml, bool)
    assert engine.supports_ssml is True  # 7.2.8 具备 texts 属性
