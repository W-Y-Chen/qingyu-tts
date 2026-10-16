# -*- coding: utf-8 -*-
"""本地语音合成引擎（Windows SAPI / pyttsx3）。

存在的理由
----------
edge-tts 复用微软 Edge 的在线合成服务，在国内部分网络环境下不可达，
典型表现是合成时报 NoAudioReceived（TCP 能连通但服务端不返回音频）。
本引擎完全离线：调用操作系统自带的语音引擎，直接产出 PCM WAV 字节，
不依赖任何外部服务，也没有商用授权风险。

代价
----
1. 音质不如云端神经网络音色（SAPI 桌面音色偏机械）；
2. 可用音色取决于操作系统安装了哪些语音包；
3. 输出为 WAV，体积比 MP3 大（约 10 倍）。

优势
----
1. 完全离线，断网可用；
2. 直接产出音频文件，能下载；
3. 停顿控制更简单——WAV 是裸 PCM，插入静音帧即可，无需 SSML 注入。
"""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import threading
import wave
from typing import Any, Dict, List, Optional

logger = logging.getLogger("qingyu.local")

try:
    import pyttsx3

    PYTTSX3_AVAILABLE = True
except Exception:  # pragma: no cover - 未安装时保持可导入
    pyttsx3 = None
    PYTTSX3_AVAILABLE = False

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except Exception:
    np = None
    NUMPY_AVAILABLE = False

_LOCALE_RE = re.compile(r"TTS_MS_([A-Za-z]{2}-[A-Za-z]{2})_")


class LocalEngineError(RuntimeError):
    """本地语音引擎不可用或合成失败。"""


def _scale_pcm(data: bytes, factor: float) -> bytes:
    """对 16bit PCM 做音量缩放。

    SAPI 的 volume 属性对"保存到文件"通常不生效，因此改为直接缩放采样值。
    """
    factor = max(0.0, min(3.0, float(factor)))
    if abs(factor - 1.0) < 0.01:
        return data

    with wave.open(io.BytesIO(data), "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())

    if params.sampwidth != 2:
        logger.warning("采样宽度非 16bit（%s 字节），跳过音量缩放", params.sampwidth)
        return data

    if NUMPY_AVAILABLE:
        arr = np.frombuffer(frames, dtype="<i2").astype(np.float32) * factor
        arr = np.clip(arr, -32768, 32767).astype("<i2")
        scaled = arr.tobytes()
    else:  # pragma: no cover - 无 numpy 时的慢路径
        import array

        a = array.array("h")
        a.frombytes(frames)
        for i in range(len(a)):
            v = int(a[i] * factor)
            a[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
        scaled = a.tobytes()

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(scaled)
    return out.getvalue()


class LocalEngine:
    """封装 pyttsx3（Windows SAPI5）。线程安全：合成过程加锁串行执行。"""

    def __init__(self) -> None:
        self.available: bool = False
        self.voices: List[Dict[str, str]] = []
        self.default_voice: Optional[str] = None
        self.reason: str = ""
        self._lock = threading.Lock()
        self._probe()

    # ------------------------------------------------------------ 探测

    def _probe(self) -> None:
        if not PYTTSX3_AVAILABLE:
            self.reason = "未安装 pyttsx3，请执行 pip install pyttsx3"
            logger.warning("本地引擎不可用：%s", self.reason)
            return
        try:
            eng = pyttsx3.init()
        except Exception as exc:  # pragma: no cover - 依赖系统组件
            self.reason = f"初始化系统语音引擎失败：{exc}"
            logger.warning("本地引擎不可用：%s", self.reason)
            return

        try:
            raw = eng.getProperty("voices") or []
        except Exception as exc:
            raw = []
            logger.warning("读取系统语音列表失败：%s", exc)
        finally:
            try:
                eng.stop()
            except Exception:
                pass

        for v in raw:
            vid = str(getattr(v, "id", "") or "")
            name = str(getattr(v, "name", "") or vid)
            m = _LOCALE_RE.search(vid)
            locale = m.group(1).replace("-", "-").upper() if m else ""
            self.voices.append({
                "id": vid,
                "name": name,
                "gender": "女" if re.search(r"huihui|yaoyao|xiaoxiao|female", vid + name, re.I) else "未知",
                "locale": locale,
                "style": "系统语音",
                "desc": "操作系统自带的本地语音，离线可用",
            })

        if not self.voices:
            self.reason = "系统未安装任何语音包"
            logger.warning("本地引擎不可用：%s", self.reason)
            return

        zh = [v for v in self.voices if v["locale"].upper().startswith("ZH")]
        self.default_voice = (zh or self.voices)[0]["id"]
        self.available = True
        logger.info(
            "本地引擎就绪：%s 个系统语音，默认 %s",
            len(self.voices), self.default_voice,
        )

    # ------------------------------------------------------------ 合成

    @staticmethod
    def _rate_to_wpm(rate_percent: int) -> int:
        """把 -50~+50 的百分比映射到 pyttsx3 的 words-per-minute。"""
        base = 200
        try:
            r = int(rate_percent)
        except (TypeError, ValueError):
            r = 0
        return int(max(80, min(400, base * (1 + r / 100))))

    def synth_text(self, text: str, voice: Optional[str], rate: int = 0, volume: int = 0) -> bytes:
        """合成一段文本，返回 WAV 字节。"""
        if not self.available:
            raise LocalEngineError(f"本地语音引擎不可用（{self.reason}）")
        if not (text or "").strip():
            raise LocalEngineError("文本为空")

        voice_id = voice if voice in {v["id"] for v in self.voices} else self.default_voice

        with self._lock:  # pyttsx3 / SAPI 不是线程安全的
            try:
                eng = pyttsx3.init()
            except Exception as exc:
                raise LocalEngineError(f"初始化语音引擎失败：{exc}") from exc
            try:
                if voice_id:
                    eng.setProperty("voice", voice_id)
                eng.setProperty("rate", self._rate_to_wpm(rate))
                data = self._save_to_bytes(eng, text)
            finally:
                try:
                    eng.stop()
                except Exception:
                    pass

        if volume:
            data = _scale_pcm(data, 1 + volume / 100)
        return data

    @staticmethod
    def _save_to_bytes(eng, text: str) -> bytes:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            eng.save_to_file(text, path)
            eng.runAndWait()
            with open(path, "rb") as fh:
                data = fh.read()
        except Exception as exc:
            raise LocalEngineError(f"本地合成失败：{exc}") from exc
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        if len(data) < 100 or data[:4] != b"RIFF":
            raise LocalEngineError("本地引擎未产生有效的音频数据")
        return data

    # ------------------------------------------------------------ 带停顿的整段渲染

    def render_items(
        self,
        items: List[Dict[str, Any]],
        voice: Optional[str],
        rate: int = 0,
        volume: int = 0,
    ) -> bytes:
        """按 text/pause 指令序列渲染整段音频。

        停顿通过插入静音帧实现——WAV 是裸 PCM，比 SSML 注入更直接可靠。
        """
        params = None
        chunks: List[tuple] = []  # ("frames", bytes) 或 ("silence", seconds)

        for item in items:
            if item["type"] == "pause":
                chunks.append(("silence", max(0, int(item["ms"])) / 1000.0))
                continue
            text = item.get("value", "").strip()
            if not text:
                continue
            data = self.synth_text(text, voice, rate, volume)
            with wave.open(io.BytesIO(data), "rb") as w:
                if params is None:
                    params = w.getparams()
                chunks.append(("frames", w.readframes(w.getnframes())))

        if params is None:
            raise LocalEngineError("没有可合成的音频内容")

        frame_bytes = params.sampwidth * params.nchannels
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(params.nchannels)
            w.setsampwidth(params.sampwidth)
            w.setframerate(params.framerate)
            for kind, payload in chunks:
                if kind == "frames":
                    w.writeframes(payload)
                else:
                    n_frames = int(params.framerate * payload)
                    w.writeframes(b"\x00" * (n_frames * frame_bytes))
        return out.getvalue()
