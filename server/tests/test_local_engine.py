# -*- coding: utf-8 -*-
"""本地语音引擎与 WAV 处理的离线测试。

合成本身依赖系统语音包，仅在可用时执行；WAV 合并、时长计算、
音量缩放、静音插入等纯逻辑始终执行。
"""

import io
import wave

import pytest

from core import audio_merger
from core.local_engine import LocalEngine, _scale_pcm


def make_wav(seconds: float, rate: int = 22050, value: int = 0) -> bytes:
    """生成一段指定时长的 WAV（默认静音）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        w.writeframes((value).to_bytes(2, "little", signed=True) * n)
    return buf.getvalue()


# ------------------------------------------------------------------ WAV 合并

def test_merge_two_wav_segments():
    merged = audio_merger.merge_wav_parts([make_wav(1.0), make_wav(2.0)])
    assert merged[:4] == b"RIFF"
    assert audio_merger.wav_duration_seconds(merged) == pytest.approx(3.0, abs=0.05)


def test_merge_single_part_returns_unchanged():
    one = make_wav(1.0)
    assert audio_merger.merge_wav_parts([one]) == one


def test_merge_empty_raises():
    with pytest.raises(ValueError):
        audio_merger.merge_wav_parts([])


def test_wav_duration_reads_header_correctly():
    assert audio_merger.wav_duration_seconds(make_wav(2.5)) == pytest.approx(2.5, abs=0.05)


def test_wav_duration_rejects_non_wav():
    assert audio_merger.wav_duration_seconds(b"not a wav") == 0.0
    assert audio_merger.wav_duration_seconds(b"") == 0.0


# ------------------------------------------------------------------ 音量缩放

def test_scale_pcm_identity_returns_same_bytes():
    data = make_wav(0.1, value=1000)
    assert _scale_pcm(data, 1.0) == data


def test_scale_pcm_changes_amplitude():
    data = make_wav(0.2, value=10000)
    halved = _scale_pcm(data, 0.5)
    with wave.open(io.BytesIO(halved), "rb") as w:
        frames = w.readframes(w.getnframes())
    assert int.from_bytes(frames[:2], "little", signed=True) == 5000


def test_scale_pcm_clips_at_maximum():
    data = make_wav(0.1, value=32000)
    loud = _scale_pcm(data, 3.0)
    with wave.open(io.BytesIO(loud), "rb") as w:
        frames = w.readframes(w.getnframes())
    assert int.from_bytes(frames[:2], "little", signed=True) == 32767


def test_scale_pcm_preserves_duration():
    data = make_wav(0.5, value=5000)
    assert audio_merger.wav_duration_seconds(_scale_pcm(data, 0.3)) == pytest.approx(0.5, abs=0.05)


# ------------------------------------------------------------------ 引擎本身

@pytest.fixture(scope="module")
def engine():
    return LocalEngine()


@pytest.mark.parametrize("percent,expected", [
    (0, 200),
    (-50, 100),
    (50, 300),
    ("abc", 200),
])
def test_rate_mapping(percent, expected):
    assert LocalEngine._rate_to_wpm(percent) == expected


def test_engine_exposes_availability_flag(engine):
    assert isinstance(engine.available, bool)
    if not engine.available:
        assert engine.reason, "不可用时必须给出原因"


@pytest.mark.skipif(not LocalEngine().available, reason="系统未安装语音包")
def test_synth_produces_valid_wav(engine):
    data = engine.synth_text("轻语，本地语音引擎测试。", engine.default_voice)
    assert data[:4] == b"RIFF"
    assert audio_merger.wav_duration_seconds(data) > 0.3


@pytest.mark.skipif(not LocalEngine().available, reason="系统未安装语音包")
def test_render_items_inserts_silence_for_pauses(engine):
    """含停顿的版本应明显长于不含停顿的版本。"""
    plain = engine.render_items(
        [{"type": "text", "value": "第一句。"}, {"type": "text", "value": "第二句。"}],
        engine.default_voice,
    )
    paused = engine.render_items(
        [
            {"type": "text", "value": "第一句。"},
            {"type": "pause", "ms": 1500},
            {"type": "text", "value": "第二句。"},
        ],
        engine.default_voice,
    )
    d_plain = audio_merger.wav_duration_seconds(plain)
    d_paused = audio_merger.wav_duration_seconds(paused)
    assert d_paused - d_plain > 1.2, f"停顿未生效：{d_plain}s -> {d_paused}s"


@pytest.mark.skipif(not LocalEngine().available, reason="系统未安装语音包")
def test_render_items_rejects_empty_content(engine):
    with pytest.raises(Exception):
        engine.render_items([], engine.default_voice)
