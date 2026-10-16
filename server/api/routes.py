# -*- coding: utf-8 -*-
"""HTTP 接口层。

接口一览：
    GET  /api/health      服务自检（含 SSML / ffmpeg 能力探测结果）
    GET  /api/voices      中文音色列表（在线优先，失败回落内置清单）
    POST /api/preview     短文本试听，同步返回音频地址
    POST /api/synthesize  提交长文本合成任务，返回 task_id
    GET  /api/tasks/<id>  查询任务进度与结果
    GET  /api/audio/<fn>  音频下载（支持 Range，保证播放器可拖动进度）
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

from flask import Blueprint, jsonify, request, send_file, Response

from config import AUDIO_DIR, FALLBACK_VOICES, Config
from core import audio_merger
from core.task_manager import TaskManager
from core.text_processor import normalize_text
from core.tts_engine import TTSEngine

# 蓝图在 register_routes 内部创建：Flask 的 Blueprint 实例注册一次后便不可再复用，
# 工厂函数可能被多次调用（测试 / 多实例部署），因此不能放在模块级。

_SAFE_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(mp3|wav)$")
_VOICE_ID_RE = re.compile(r"^[A-Za-z]{2,5}-[A-Za-z]{2}-[A-Za-z0-9]+Neural$")

# 音色缓存：启动即用内置清单，联网成功后被在线列表覆盖
_voice_cache: Dict[str, Any] = {
    "voices": list(FALLBACK_VOICES),
    "source": "fallback",
    "updated_at": 0.0,
}
_VOICE_TTL = 6 * 3600


# ------------------------------------------------------------------ 工具

def _clamp(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, v))


def _known_voice_ids() -> set:
    return {v["id"] for v in _voice_cache["voices"] if v.get("id")}


def _validate(data: Dict[str, Any]) -> Tuple[str, str, int, int]:
    """校验并规范化请求参数，非法输入直接抛 ValueError。"""
    text = normalize_text(data.get("text", ""))
    if not text:
        raise ValueError("文本为空")
    if len(text) > Config.MAX_TEXT_CHARS:
        raise ValueError(f"文本超出上限（最多 {Config.MAX_TEXT_CHARS} 字）")

    voice = str(data.get("voice") or Config.DEFAULT_VOICE).strip()
    if not _VOICE_ID_RE.match(voice):
        raise ValueError("音色标识不合法")
    known = _known_voice_ids()
    if known and voice not in known:
        raise ValueError("音色不在可用列表中，请先刷新音色列表")

    rate = _clamp(data.get("rate", 0), Config.RATE_MIN, Config.RATE_MAX)
    volume = _clamp(data.get("volume", 0), Config.VOLUME_MIN, Config.VOLUME_MAX)
    return text, voice, rate, volume


AUDIO_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav"}


def _save_audio(data: bytes, ext: str = "mp3") -> Tuple[str, str]:
    ext = ext if ext in AUDIO_MIME else "mp3"
    filename = f"{uuid.uuid4().hex}.{ext}"
    (Path(AUDIO_DIR) / filename).write_bytes(data)
    return filename, f"/api/audio/{filename}"


def _send_audio(path: Path) -> Response:
    """支持 HTTP Range 的文件响应，便于前端拖动播放进度。"""
    file_size = path.stat().st_size
    range_header = request.headers.get("Range")
    mime = AUDIO_MIME.get(path.suffix.lstrip("."), "audio/mpeg")

    if not range_header:
        resp = send_file(str(path), mimetype=mime, conditional=True)
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(file_size)
        resp.headers["Cache-Control"] = "public, max-age=600"
        return resp

    start, end = 0, file_size - 1
    m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if m:
        if m.group(1):
            start = int(m.group(1))
        if m.group(2):
            end = min(int(m.group(2)), file_size - 1)
    if start >= file_size or start > end:
        return Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})

    length = end - start + 1
    with open(path, "rb") as fh:
        fh.seek(start)
        chunk = fh.read(length)

    resp = Response(chunk, status=206, mimetype=mime)
    resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    resp.headers["Content-Length"] = str(length)
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


# ------------------------------------------------------------------ 接口

def register_routes(app, engine: TTSEngine, tasks: TaskManager) -> None:
    """把路由注册到 app，并把引擎与任务管理器注入闭包。"""
    api = Blueprint("api", __name__, url_prefix="/api")

    @api.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "轻语 TTS",
            "ssml_pause": engine.supports_ssml,
            "ffmpeg_merge": audio_merger.ffmpeg_available(),
            "max_text_chars": Config.MAX_TEXT_CHARS,
            "segment_max_chars": Config.SEGMENT_MAX_CHARS,
            "voices": len(_voice_cache["voices"]),
            "voice_source": _voice_cache["source"],
            "engine": engine.active_backend,
            "engine_degraded": engine.degraded,
            "local_available": engine.local.available,
            "local_reason": engine.local.reason or "",
        })

    @api.get("/voices")
    def voices():
        fresh = (
            _voice_cache["source"] == "online"
            and time.time() - _voice_cache["updated_at"] < _VOICE_TTL
        )
        if not fresh:
            try:
                vs = asyncio.run(engine.list_voices())
                if vs:
                    if engine.active_backend == "local":
                        # 本地引擎的音色 ID 是系统注册表路径，不能与云端清单混合
                        _voice_cache.update({
                            "voices": vs, "source": "local", "updated_at": time.time()
                        })
                    else:
                        merged = {v["id"]: v for v in FALLBACK_VOICES}
                        for v in vs:
                            merged[v["id"]] = v
                        _voice_cache.update({
                            "voices": list(merged.values()),
                            "source": "fallback" if vs == FALLBACK_VOICES else "online",
                            "updated_at": time.time(),
                        })
            except Exception as exc:  # noqa: BLE001 - 网络不可用时静默回落
                app.logger.warning("获取音色列表失败，使用内置清单：%s", exc)
        return jsonify({
            "voices": _voice_cache["voices"],
            "source": _voice_cache["source"],
            "count": len(_voice_cache["voices"]),
        })

    @api.post("/preview")
    def preview():
        payload = request.get_json(silent=True) or {}
        try:
            text, voice, rate, volume = _validate(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        clipped = text[: Config.PREVIEW_MAX_CHARS]
        try:
            data, stats = asyncio.run(
                engine.synth_text(
                    clipped, voice, rate=rate, volume=volume,
                    max_chars=Config.PREVIEW_MAX_CHARS,
                )
            )
        except Exception as exc:  # noqa: BLE001
            app.logger.error("试听合成失败：%s", exc)
            return jsonify({"error": f"合成失败：{exc}"}), 502

        _, url = _save_audio(data, stats.get("ext", "mp3"))
        return jsonify({
            "audio_url": url,
            "duration": stats["duration"],
            "chars": stats["chars"],
            "clipped": len(text) > Config.PREVIEW_MAX_CHARS,
            "elapsed": stats["elapsed"],
            "engine": stats.get("engine", "edge"),
            "ext": stats.get("ext", "mp3"),
        })

    @api.post("/synthesize")
    def synthesize():
        payload = request.get_json(silent=True) or {}
        try:
            text, voice, rate, volume = _validate(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        task = tasks.create(text, voice, rate, volume)
        return jsonify({"task_id": task.id, "chars": task.chars, "voice": voice})

    @api.get("/tasks/<task_id>")
    def task_status(task_id: str):
        task = tasks.get(task_id)
        if task is None:
            return jsonify({"error": "任务不存在或已过期"}), 404
        return jsonify(task.to_dict())

    @api.get("/audio/<filename>")
    def audio(filename: str):
        if not _SAFE_NAME_RE.match(filename):
            return jsonify({"error": "非法文件名"}), 400
        path = Path(AUDIO_DIR) / filename
        if not path.exists():
            return jsonify({"error": "音频不存在或已过期"}), 404
        return _send_audio(path)

    @api.errorhandler(404)
    def _not_found(_e):
        return jsonify({"error": "接口不存在"}), 404

    app.register_blueprint(api)
