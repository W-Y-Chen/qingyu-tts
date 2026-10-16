# -*- coding: utf-8 -*-
"""文本预处理模块的离线单元测试（不依赖网络）。"""

import pytest

from config import Config
from core.text_processor import (
    build_segments,
    count_pause_markers,
    normalize_text,
    parse_duration,
    parse_pause_items,
    split_sentences,
    strip_pause_markers,
)


# ------------------------------------------------------------------ 归一化

def test_normalize_removes_bom_and_control_chars():
    raw = "﻿你好\x07世界\r\n第二行"
    out = normalize_text(raw)
    assert "﻿" not in out
    assert "\x07" not in out
    assert "\r" not in out


def test_normalize_single_newline_becomes_space():
    assert normalize_text("第一行\n第二行") == "第一行 第二行"


def test_normalize_keeps_paragraph_break():
    out = normalize_text("第一段\n\n第二段")
    assert out == "第一段\n\n第二段"


# ------------------------------------------------------------------ 停顿解析

@pytest.mark.parametrize("token,expected", [
    (None, Config.DEFAULT_PAUSE_MS),
    ("300", 300),
    ("300ms", 300),
    ("1.5s", 1500),
    ("0", 0),
    ("99999", Config.MAX_PAUSE_MS),
    ("abc", Config.DEFAULT_PAUSE_MS),
])
def test_parse_duration(token, expected):
    assert parse_duration(token) == expected


def test_parse_pause_items_basic():
    items = parse_pause_items("你好[p500]世界")
    assert [i["type"] for i in items] == ["text", "pause", "text"]
    assert items[1]["ms"] == 500
    assert items[0]["value"] == "你好"
    assert items[2]["value"] == "世界"


def test_parse_pause_items_supports_chinese_marker():
    items = parse_pause_items("开场[停顿1s]继续")
    assert items[1] == {"type": "pause", "ms": 1000}


def test_count_pause_markers():
    assert count_pause_markers("a[p]b[停顿2s]c") == 2
    assert strip_pause_markers("a[p]b[停顿2s]c") == "abc"


# ------------------------------------------------------------------ 句子切分

def test_split_sentences_keeps_punctuation():
    sents = split_sentences("你好。世界！再见？")
    assert sents == ["你好。", "世界！", "再见？"]


def test_split_sentences_does_not_split_before_closing_quote():
    """右引号前的句末标点不应触发切分，否则会把引号甩到下一句开头。"""
    sents = split_sentences("他说：“走吧。”然后离开。")
    assert sents == ["他说：“走吧。”然后离开。"]


def test_split_sentences_splits_normally():
    assert split_sentences("你好。世界。") == ["你好。", "世界。"]


# ------------------------------------------------------------------ 分段

def test_build_segments_respects_max_chars():
    text = "。".join(f"第{i}句话内容" for i in range(200))
    for seg in build_segments(text, max_chars=100):
        size = sum(len(i["value"]) for i in seg if i["type"] == "text")
        assert size <= 100


def test_build_segments_splits_on_sentence_boundary():
    text = "第一句话。" * 50
    segments = build_segments(text, max_chars=20)
    assert len(segments) > 1
    for seg in segments:
        joined = "".join(i["value"] for i in seg if i["type"] == "text")
        assert joined.endswith("。")


def test_build_segments_inserts_paragraph_pause():
    segments = build_segments("第一段。\n\n第二段。", max_chars=500)
    pauses = [i for seg in segments for i in seg if i["type"] == "pause"]
    assert [p["ms"] for p in pauses] == [Config.PARAGRAPH_PAUSE_MS]


def test_build_segments_handles_oversized_sentence():
    long_sentence = "啊" * 1000
    segments = build_segments(long_sentence, max_chars=300)
    total = sum(len(i["value"]) for seg in segments for i in seg if i["type"] == "text")
    assert total == 1000
    assert len(segments) == 4


def test_build_segments_empty_input():
    assert build_segments("") == []
    assert build_segments("   \n\n  ") == []


def test_pause_does_not_consume_char_budget():
    text = "短句。" + "[p2000]" * 20 + "结尾。"
    segments = build_segments(text, max_chars=50)
    assert len(segments) == 1
