# -*- coding: utf-8 -*-
"""分段音频合并模块。

长文本按段合成后会产生多个 MP3 片段，需要合并为单一文件供小程序播放与下载。

实现策略（按可用性自动选择）：
1. 系统存在 ffmpeg 时，使用 concat 解复用器以 `-c copy` 无损合并，
   时长元数据正确、播放器进度条准确；
2. 无 ffmpeg 时退化为二进制拼接。edge-tts 输出的片段参数一致
   （24kHz / 48kbps / 单声道 CBR），主流解码器可连续播放，
   但部分播放器显示的时长可能与实际存在偏差。

该降级行为会被写入日志，服务端 /api/health 亦会返回当前合并方式，
避免"静默降级"造成排查困难。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

logger = logging.getLogger("qingyu.merger")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def merge_audio_parts(parts: List[bytes], out_path: str | Path) -> str:
    """把若干 MP3 片段合并为单个文件，返回实际使用的合并方式。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not parts:
        raise ValueError("没有可合并的音频片段")

    if len(parts) == 1:
        out_path.write_bytes(parts[0])
        return "single"

    if ffmpeg_available():
        try:
            _merge_with_ffmpeg(parts, out_path)
            return "ffmpeg"
        except Exception as exc:  # pragma: no cover - 依赖外部二进制
            logger.warning("ffmpeg 合并失败，回退到二进制拼接：%s", exc)

    out_path.write_bytes(b"".join(parts))
    return "concat"


def merge_audio_parts_to_bytes(parts: List[bytes]) -> bytes:
    """内存版合并：优先走 ffmpeg，失败则二进制拼接。返回 MP3 字节。"""
    if not parts:
        raise ValueError("没有可合并的音频片段")
    if len(parts) == 1:
        return parts[0]
    if ffmpeg_available():
        with tempfile.TemporaryDirectory(prefix="qingyu_") as tmp:
            out = Path(tmp) / "merged.mp3"
            try:
                _merge_with_ffmpeg(parts, out)
                data = out.read_bytes()
                if data:
                    return data
            except Exception as exc:  # pragma: no cover - 依赖外部二进制
                logger.warning("ffmpeg 内存合并失败，回退到二进制拼接：%s", exc)
    return b"".join(parts)


def _merge_with_ffmpeg(parts: List[bytes], out_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="qingyu_") as tmp:
        tmp_dir = Path(tmp)
        list_file = tmp_dir / "list.txt"
        entries = []
        for idx, part in enumerate(parts):
            seg = tmp_dir / f"seg_{idx:04d}.mp3"
            seg.write_bytes(part)
            entries.append(f"file '{seg.as_posix()}'")
        list_file.write_text("\n".join(entries), encoding="utf-8")

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "ignore")[:300])


def merge_wav_parts(parts: List[bytes]) -> bytes:
    """合并多个 WAV 片段。

    WAV 带 44 字节的文件头，不能像 MP3 那样直接拼接字节，
    必须解出 PCM 帧后统一重写。各片段参数需一致（同一引擎同一音色即满足）。
    """
    import io
    import wave

    if not parts:
        raise ValueError("没有可合并的音频片段")
    if len(parts) == 1:
        return parts[0]

    params = None
    frames: List[bytes] = []
    for part in parts:
        with wave.open(io.BytesIO(part), "rb") as w:
            if params is None:
                params = w.getparams()
            frames.append(w.readframes(w.getnframes()))

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(b"".join(frames))
    return out.getvalue()


def wav_duration_seconds(data: bytes) -> float:
    """从 WAV 头精确读取时长（秒）。"""
    import io
    import wave

    if not data or data[:4] != b"RIFF":
        return 0.0
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 2)
    except Exception:
        return 0.0


def estimate_duration_seconds(data: bytes, bitrate_kbps: int = 48) -> float:
    """按 CBR 码率估算 MP3 时长（秒）。

    edge-tts 输出固定为 48kbps CBR，因此字节数可线性换算为时长。
    仅用于前端展示，不参与字幕/同步等对精度敏感的用途。
    """
    if not data:
        return 0.0
    return round(len(data) * 8 / (bitrate_kbps * 1000), 2)
