# -*- coding: utf-8 -*-
"""语音合成引擎封装。

对 edge-tts 做一层薄封装，解决三个工程问题：

1. **自定义停顿标记**
   edge-tts 的公开 API 会对输入做 XML 转义（Communicate.__init__ 中调用
   `escape(remove_incompatible_characters(text))`），因此 `<break time="500ms"/>`
   这类 SSML 标签无法作为普通文本透传，会被转义成实体而失效。
   本模块通过重写 Communicate 的 `texts` 属性（该属性在 __init__ 之后
   不再被转义处理）注入自构造的 SSML 片段，从而让 `<break>` 生效。
   `supports_ssml` 会在启动时探测该属性是否存在，不存在则自动降级为
   无停顿的纯文本合成，不会抛异常。

2. **长文本分段**
   按句子边界切分为多段并行/串行合成，规避单次请求字节上限。

3. **网络抖动**
   单段合成失败自动重试，超时可配置。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from edge_tts import Communicate

from config import FALLBACK_VOICES, Config
from core import audio_merger
from core.local_engine import LocalEngine
from core.text_processor import build_segments, count_chars, normalize_text

logger = logging.getLogger("qingyu.tts")

try:  # edge-tts 内部工具：剔除会导致服务端报错的字符
    from edge_tts.util import remove_incompatible_characters as _clean
except Exception:  # pragma: no cover
    def _clean(text: str) -> str:
        return text


# ------------------------------------------------------------------ SSML 构造

def xml_escape(text: str) -> str:
    """仅转义文本内容中需要转义的三个字符，避免破坏自行注入的标签。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def items_to_ssml(items: List[Dict[str, Any]]) -> str:
    """把 text/pause 指令序列渲染为可注入的 SSML 片段。

    注意：产出的是 SSML 的"内层内容"，不含 <speak> 根标签，
    因为 edge-tts 的 mkssml() 会在外层补上 <speak><voice><prosody>。
    """
    buf: List[str] = []
    for item in items:
        if item["type"] == "pause":
            buf.append(f'<break time="{int(item["ms"])}ms"/>')
        else:
            buf.append(xml_escape(_clean(item["value"])))
    return "".join(buf)


def items_to_plain(items: List[Dict[str, Any]]) -> str:
    """降级路径：丢弃停顿指令，仅保留纯文本。"""
    return "".join(_clean(i["value"]) for i in items if i["type"] == "text")


_ATOM_RE = re.compile(r"<[^>]*>|[^<]+")


def pack_by_bytes(content: str, limit: int) -> List[str]:
    """按字节上限切分 SSML 内容，且不切断标签。

    把内容拆成"标签原子"和"文本原子"后装箱；单个原子超限时按 UTF-8
    字符边界硬切（此时该片段本身已不可解析，但保证不超限）。
    """
    chunks: List[str] = []
    cur: List[str] = []
    cur_bytes = 0
    for atom in _ATOM_RE.findall(content):
        size = len(atom.encode("utf-8"))
        if size > limit:
            if cur:
                chunks.append("".join(cur))
                cur, cur_bytes = [], 0
            chunks.extend(_hard_split_str(atom, limit))
            continue
        if cur_bytes + size > limit and cur:
            chunks.append("".join(cur))
            cur, cur_bytes = [], 0
        cur.append(atom)
        cur_bytes += size
    if cur:
        chunks.append("".join(cur))
    return chunks or [""]


def _hard_split_str(text: str, limit: int) -> List[str]:
    out, buf, size = [], "", 0
    for ch in text:
        b = len(ch.encode("utf-8"))
        if size + b > limit and buf:
            out.append(buf)
            buf, size = "", 0
        buf += ch
        size += b
    if buf:
        out.append(buf)
    return out


# ------------------------------------------------------------------ 引擎

class TTSSynthesisError(RuntimeError):
    """语音合成失败（含重试耗尽）。"""


def _failure_hint(err: BaseException) -> str:
    """把底层异常翻译成可操作的排查方向。

    常见失败是 edge-tts 的 NoAudioReceived——TCP 能连通但服务端未返回音频，
    多见于网络受限、系统时间偏差或需要代理的环境。
    """
    text = str(err).lower()
    if "no audio" in text:
        return (
            "。可能原因：① 服务器所在网络无法访问微软语音服务；"
            "② 系统时间偏差超过 5 分钟导致鉴权失败；"
            "③ 该网络需要配置代理。"
            "诊断方法：在 server 目录运行 python ../tools/diagnose_tts.py"
        )
    if "timed out" in text or "timeout" in text:
        return "。可能是网络过慢或服务端无响应，可调大 QINGYU_SYNTH_TIMEOUT 或更换网络。"
    return ""


class TTSEngine:
    """edge-tts 封装：分段合成 + SSML 停顿注入 + 失败重试。"""

    def __init__(self) -> None:
        self.supports_ssml: bool = self._probe_ssml()
        logger.info("SSML 停顿注入能力：%s", "启用" if self.supports_ssml else "不可用（已降级）")

        # 本地引擎（离线兜底）。edge 不可用时会自动切到这里。
        self.local = LocalEngine()
        self.backend = Config.ENGINE if Config.ENGINE in ("edge", "local") else "edge"
        if self.backend == "local" and not self.local.available:
            logger.warning("配置为 local，但本地引擎不可用（%s），回退到 edge", self.local.reason)
            self.backend = "edge"
        self.active_backend: str = self.backend
        self.degraded: bool = False
        logger.info("当前合成引擎：%s（本地引擎可用：%s）", self.active_backend, self.local.available)

    def _use_local(self) -> bool:
        return self.active_backend == "local" and self.local.available

    @staticmethod
    def _probe_ssml() -> bool:
        """探测当前 edge-tts 版本是否允许注入 SSML。

        依据：Communicate.__init__ 会把 `texts` 作为实例属性保存，
        并在流式发送时原样取出。只要该属性存在即可注入。
        探测失败不影响服务启动。
        """
        try:
            probe = Communicate("探测", Config.DEFAULT_VOICE)
            return hasattr(probe, "texts")
        except Exception as exc:  # pragma: no cover
            logger.warning("SSML 能力探测失败，按不可用处理：%s", exc)
            return False

    @staticmethod
    def format_percent(value: int, minimum: int, maximum: int) -> str:
        """把整数百分比格式化为 edge-tts 要求的 '+0%' 形式，并夹紧范围。"""
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = 0
        v = max(minimum, min(maximum, v))
        return f"{v:+d}%"

    # -------------------------------------------------------------- 单次请求

    async def _request_once(
        self,
        content: str,
        voice: str,
        rate: str,
        volume: str,
    ) -> bytes:
        comm = Communicate(
            "轻语",  # 占位文本，随后被 texts 覆盖
            voice,
            rate=rate,
            volume=volume,
        )
        # texts 原生为 UTF-8 字节序列（split_text_by_byte_length 产出 bytes），
        # 这里保持一致，mkssml 会自动解码。
        comm.texts = [
            chunk.encode("utf-8")
            for chunk in pack_by_bytes(content, Config.SSML_CHUNK_BYTES)
        ]

        audio = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        if not audio:
            raise TTSSynthesisError("服务端未返回音频数据")
        return bytes(audio)

    async def synth_items(
        self,
        items: List[Dict[str, Any]],
        voice: str,
        rate: str,
        volume: str,
    ) -> bytes:
        """合成单个分段，含重试与超时控制。"""
        content = items_to_ssml(items) if self.supports_ssml else items_to_plain(items)
        last_error: Optional[BaseException] = None
        for attempt in range(1, Config.SYNTH_RETRY + 1):
            try:
                return await asyncio.wait_for(
                    self._request_once(content, voice, rate, volume),
                    timeout=Config.SYNTH_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning("第 %s 次合成超时（>%ss）", attempt, Config.SYNTH_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - 统一收敛为领域异常
                last_error = exc
                logger.warning("第 %s 次合成失败：%s", attempt, exc)
            if attempt < Config.SYNTH_RETRY:
                await asyncio.sleep(0.6 * attempt)
        raise TTSSynthesisError(
            f"分段合成失败（已重试 {Config.SYNTH_RETRY} 次）：{last_error}"
            + _failure_hint(last_error)
        )

    # -------------------------------------------------------------- 全文合成

    async def synth_text(
        self,
        text: str,
        voice: str,
        rate: int = 0,
        volume: int = 0,
        max_chars: Optional[int] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[bytes, Dict[str, Any]]:
        """合成整篇文本，返回 (音频字节, 统计信息)。

        `progress_cb(done, total)` 用于在长任务中回写进度。
        引擎选择：优先配置值；edge 失败且开启自动降级时切到本地引擎重试。
        """
        if self._use_local():
            return await self._synth_local(text, voice, rate, volume, max_chars, progress_cb)

        try:
            return await self._synth_edge(text, voice, rate, volume, max_chars, progress_cb)
        except Exception as exc:
            if Config.AUTO_FALLBACK and self.local.available:
                logger.warning("edge 引擎合成失败，自动降级到本地引擎：%s", exc)
                self.degraded = True
                self.active_backend = "local"
                return await self._synth_local(text, voice, rate, volume, max_chars, progress_cb)
            raise

    async def _synth_local(
        self,
        text: str,
        voice: str,
        rate: int,
        volume: int,
        max_chars: Optional[int],
        progress_cb: Optional[Callable[[int, int], None]],
    ) -> Tuple[bytes, Dict[str, Any]]:
        """本地引擎路径：同步阻塞调用，放进线程池避免卡住事件循环。"""
        loop = asyncio.get_running_loop()
        started = time.time()

        segments = build_segments(normalize_text(text), max_chars)
        if not segments:
            raise ValueError("文本为空或不含有效内容")

        parts: List[bytes] = []
        total = len(segments)
        for idx, seg in enumerate(segments, start=1):
            data = await loop.run_in_executor(
                None, lambda s=seg: self.local.render_items(s, voice, rate, volume)
            )
            parts.append(data)
            if progress_cb:
                progress_cb(idx, total)

        merged = audio_merger.merge_wav_parts(parts)
        stats = {
            "chars": count_chars([i for seg in segments for i in seg]),
            "segments": total,
            "pause_count": sum(1 for seg in segments for i in seg if i["type"] == "pause"),
            "elapsed": round(time.time() - started, 2),
            "bytes": len(merged),
            "duration": audio_merger.wav_duration_seconds(merged),
            "ssml": False,
            "ext": "wav",
            "engine": "local",
        }
        return merged, stats

    async def _synth_edge(
        self,
        text: str,
        voice: str,
        rate: int,
        volume: int,
        max_chars: Optional[int],
        progress_cb: Optional[Callable[[int, int], None]],
    ) -> Tuple[bytes, Dict[str, Any]]:
        """edge-tts 云端路径。"""
        started = time.time()
        clean_text = normalize_text(text)
        segments = build_segments(clean_text, max_chars)
        if not segments:
            raise ValueError("文本为空或不含有效内容")

        rate_str = self.format_percent(rate, Config.RATE_MIN, Config.RATE_MAX)
        volume_str = self.format_percent(volume, Config.VOLUME_MIN, Config.VOLUME_MAX)

        parts: List[bytes] = []
        total = len(segments)
        for idx, seg in enumerate(segments, start=1):
            data = await self.synth_items(seg, voice, rate_str, volume_str)
            parts.append(data)
            if progress_cb:
                progress_cb(idx, total)

        merged = b"".join(parts) if len(parts) == 1 else audio_merger.merge_audio_parts_to_bytes(parts)
        stats = {
            "chars": count_chars([i for seg in segments for i in seg]),
            "segments": total,
            "pause_count": sum(1 for seg in segments for i in seg if i["type"] == "pause"),
            "elapsed": round(time.time() - started, 2),
            "bytes": len(merged),
            "duration": audio_merger.estimate_duration_seconds(merged),
            "ssml": self.supports_ssml,
            "ext": "mp3",
            "engine": "edge",
        }
        return merged, stats

    # -------------------------------------------------------------- 音色列表

    async def list_voices(self) -> List[Dict[str, str]]:
        """按当前生效的引擎返回音色列表。"""
        if self._use_local():
            return [
                {
                    "id": v["id"], "name": v["name"], "gender": v["gender"],
                    "locale": v["locale"], "style": v["style"], "desc": v["desc"],
                }
                for v in self.local.voices
            ]
        # 注意：音色列表获取失败时不要切换 active_backend。
        # 两个引擎的音色 ID 属于不同命名空间（云端短名 vs 系统注册表路径），
        # 混用会让前端传回的音色 ID 校验失败。引擎切换只在合成失败时发生。
        try:
            online = await self.fetch_voices()
            if online:
                return online
        except Exception as exc:
            logger.warning("拉取在线音色失败：%s", exc)
        return list(FALLBACK_VOICES)
    @staticmethod
    async def fetch_voices() -> List[Dict[str, str]]:
        """从 edge-tts 拉取在线中文音色列表（需外网）。"""
        from edge_tts import list_voices  # 局部导入，避免启动期网络依赖

        from config import VOICE_LANG_PREFIXES

        raw = await list_voices()
        result = []
        for v in raw:
            locale = v.get("Locale", "")
            if not locale.startswith(VOICE_LANG_PREFIXES):
                continue
            result.append({
                "id": v.get("ShortName", ""),
                "name": v.get("FriendlyName", v.get("ShortName", "")),
                "gender": {"Female": "女", "Male": "男"}.get(v.get("Gender", ""), "未知"),
                "locale": locale,
                "style": v.get("VoiceTag", {}).get("VoicePersonalities") or "",
                "desc": "",
            })
        return [v for v in result if v["id"]]
