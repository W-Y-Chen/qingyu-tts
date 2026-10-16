# -*- coding: utf-8 -*-
"""文本预处理模块。

职责：
1. 归一化：去 BOM、统一换行、剔除不可见控制字符；
2. 停顿标记解析：把用户书写的可读标记翻译成结构化停顿指令；
3. 智能分段：在句子边界切分长文本，保证单次请求不超过服务端的字节上限。

设计约束（对应设计书中的"长文本自动分段"与"自定义停顿标记"两项功能）：
- 分段必须落在句子边界，避免把一句话拦腰截断导致断句生硬；
- 超长单句（无标点，如乱码或超长 URL）按字数硬切，保证不超限；
- 停顿指令不占用字数预算，单独成项。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from config import Config

# ---------------------------------------------------------------- 归一化

_BOM = "﻿"
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """清洗输入文本，保留段落结构（空行），去掉不可见控制字符。"""
    if raw is None:
        return ""
    text = str(raw).replace(_BOM, "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL_RE.sub("", text)
    text = text.replace("\u00a0", " ").replace("\u3000", "  ")
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    # 单个换行视为同一段落内的软换行，转换为空格保持朗读连贯
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return text.strip()


# ---------------------------------------------------------------- 停顿标记

# 支持：[p] / [p300] / [p300ms] / [p1.5s] / [停顿] / [停顿800]
_PAUSE_RE = re.compile(
    r"\[\s*(?:p|停顿)\s*[:：]?\s*(\d+(?:\.\d+)?\s*(?:ms|s)?)?\s*\]",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s)?")


def parse_duration(token: str | None) -> int:
    """把 '300' / '300ms' / '1.5s' 解析为毫秒，并夹紧到合法区间。"""
    if not token:
        return Config.DEFAULT_PAUSE_MS
    m = _NUMBER_RE.match(str(token).strip().lower())
    if not m:
        return Config.DEFAULT_PAUSE_MS
    value = float(m.group(1))
    unit = m.group(2)
    ms = value * 1000 if unit == "s" else value
    return int(max(0, min(Config.MAX_PAUSE_MS, round(ms))))


def parse_pause_items(text: str) -> List[Dict[str, Any]]:
    """把一段文本解析为 text / pause 交替的指令序列。"""
    items: List[Dict[str, Any]] = []
    cursor = 0
    for m in _PAUSE_RE.finditer(text):
        if m.start() > cursor:
            items.append({"type": "text", "value": text[cursor:m.start()]})
        items.append({"type": "pause", "ms": parse_duration(m.group(1))})
        cursor = m.end()
    if cursor < len(text):
        items.append({"type": "text", "value": text[cursor:]})
    return items


def split_paragraphs(text: str) -> List[str]:
    """按空行切分段落。"""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def parse_full_items(text: str) -> List[Dict[str, Any]]:
    """完整解析：段落之间自动插入停顿，段落内部解析用户停顿标记。"""
    paragraphs = split_paragraphs(text)
    items: List[Dict[str, Any]] = []
    for idx, para in enumerate(paragraphs):
        if idx > 0:
            items.append({"type": "pause", "ms": Config.PARAGRAPH_PAUSE_MS})
        items.extend(parse_pause_items(para))
    return items


# ---------------------------------------------------------------- 句子切分

# 句末标点；负向断言避免把右引号/右括号留在上一句之外
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?…；;])(?![”’\"'』」〉）》)\]】])")
_HARD_BREAK_HINT = "，,、；;：: "


def split_sentences(text: str) -> List[str]:
    """在句末标点处切句，保留标点本身。"""
    parts = [p for p in _SENT_SPLIT_RE.split(text) if p and p.strip()]
    return [p.strip() for p in parts] if parts else ([text.strip()] if text.strip() else [])


def hard_split(sentence: str, max_chars: int) -> List[str]:
    """对超过 max_chars 的单句做二次切分，尽量落在逗号等次级停顿处。"""
    if len(sentence) <= max_chars:
        return [sentence]
    pieces: List[str] = []
    buf = sentence
    while len(buf) > max_chars:
        cut = -1
        window_start = int(max_chars * 0.6)
        for i in range(max_chars, window_start, -1):
            if buf[i - 1] in _HARD_BREAK_HINT:
                cut = i
                break
        if cut == -1:
            cut = max_chars
        pieces.append(buf[:cut].strip())
        buf = buf[cut:]
    if buf.strip():
        pieces.append(buf.strip())
    return [p for p in pieces if p]


# ---------------------------------------------------------------- 分段

def build_segments(text: str, max_chars: int | None = None) -> List[List[Dict[str, Any]]]:
    """把整篇文本切分为若干"合成段"。

    每个合成段是一个 items 列表（text 与 pause 混合），
    直接交给 SSML 构造器渲染。返回值不含空段。
    """
    max_chars = max_chars or Config.SEGMENT_MAX_CHARS
    items = parse_full_items(normalize_text(text))
    if not items:
        return []

    segments: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            segments.append(current)
            current = []
            current_len = 0

    for item in items:
        if item["type"] == "pause":
            current.append(item)
            continue
        for sentence in split_sentences(item["value"]):
            for piece in hard_split(sentence, max_chars):
                if current_len + len(piece) > max_chars and current_len > 0:
                    flush()
                current.append({"type": "text", "value": piece})
                current_len += len(piece)
    flush()
    return [seg for seg in segments if any(i["type"] == "text" and i["value"].strip() for i in seg)]


def count_chars(items: List[Dict[str, Any]]) -> int:
    """统计指令序列中的实际朗读字数（停顿不计）。"""
    return sum(len(i["value"]) for i in items if i["type"] == "text")


def strip_pause_markers(text: str) -> str:
    """移除所有停顿标记，用于无 SSML 能力时的降级路径。"""
    return _PAUSE_RE.sub("", text)


def count_pause_markers(text: str) -> int:
    """统计文本中显式书写的停顿标记数量，供前端提示。"""
    return len(_PAUSE_RE.findall(text))
