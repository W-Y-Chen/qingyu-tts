# -*- coding: utf-8 -*-
"""轻语 TTS 服务端配置。

配置优先级：环境变量 > .env 文件 > 代码默认值。
所有阈值均为可运行的实测默认值，而非占位。
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "storage" / "audio"

try:  # python-dotenv 为可选依赖
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover - 未安装时静默降级
    pass


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # ---- 服务 ----
    HOST = os.environ.get("QINGYU_HOST", "0.0.0.0")
    PORT = _env_int("QINGYU_PORT", 5000)
    DEBUG = _env_bool("QINGYU_DEBUG", True)

    # ---- 合成参数 ----
    # edge  = edge-tts（云端，音质好，需能访问微软服务）
    # local = 系统本地引擎（离线，音质一般，一定可用）
    ENGINE = os.environ.get("QINGYU_ENGINE", "edge").strip().lower()
    # edge 合成失败时是否自动降级到本地引擎（强烈建议保持开启）
    AUTO_FALLBACK = _env_bool("QINGYU_AUTO_FALLBACK", True)

    DEFAULT_VOICE = os.environ.get("QINGYU_DEFAULT_VOICE", "zh-CN-XiaoxiaoNeural")
    RATE_MIN, RATE_MAX = -50, 50      # 百分比
    VOLUME_MIN, VOLUME_MAX = -50, 50  # 百分比
    PITCH_MIN, PITCH_MAX = -50, 50

    # ---- 文本与分段 ----
    MAX_TEXT_CHARS = _env_int("QINGYU_MAX_TEXT_CHARS", 20000)   # 单次请求文本上限
    SEGMENT_MAX_CHARS = _env_int("QINGYU_SEGMENT_MAX_CHARS", 400)  # 单段合成字数
    PREVIEW_MAX_CHARS = _env_int("QINGYU_PREVIEW_MAX_CHARS", 200)  # 试听字数
    SSML_CHUNK_BYTES = 4096  # edge-tts 单次 SSML 请求字节上限

    # ---- 停顿（毫秒）----
    DEFAULT_PAUSE_MS = 400        # [p] 无参数时的默认停顿
    PARAGRAPH_PAUSE_MS = 500      # 空行分段处的自动停顿
    MAX_PAUSE_MS = 5000           # 单处停顿上限，防止恶意输入

    # ---- 并发与任务 ----
    MAX_WORKERS = _env_int("QINGYU_MAX_WORKERS", 3)
    TASK_TTL_SECONDS = _env_int("QINGYU_TASK_TTL", 1800)     # 任务状态保留时长
    # 音频保留时长。健身、通勤等场景会长时间反复收听同一份音频，
    # 30 分钟太短（听到一半链接就失效），默认给 2 小时。
    AUDIO_TTL_SECONDS = _env_int("QINGYU_AUDIO_TTL", 7200)
    SYNTH_RETRY = _env_int("QINGYU_SYNTH_RETRY", 3)          # 单段失败重试次数
    SYNTH_TIMEOUT = _env_int("QINGYU_SYNTH_TIMEOUT", 60)     # 单段超时（秒）

    # ---- 安全 ----
    ALLOWED_AUDIO_EXTS = {".mp3"}
    CORS_ORIGINS = os.environ.get("QINGYU_CORS", "*")

    @classmethod
    def ensure_dirs(cls) -> None:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# 中文音色兜底清单：当无法联网拉取 edge-tts 官方音色列表时使用。
# 字段与 /api/voices 在线结果保持一致，前端无需区分来源。
FALLBACK_VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "gender": "女",
     "style": "温柔亲和", "desc": "通用女声，适合小说、课件、长文朗读"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "gender": "女",
     "style": "活泼明亮", "desc": "适合儿童内容、短视频配音"},
    {"id": "zh-CN-XiaochenNeural", "name": "晓辰", "gender": "女",
     "style": "甜美轻快", "desc": "适合播客、生活类内容"},
    {"id": "zh-CN-XiaohanNeural", "name": "晓涵", "gender": "女",
     "style": "温暖沉静", "desc": "适合散文、睡前读物"},
    {"id": "zh-CN-XiaomengNeural", "name": "晓梦", "gender": "女",
     "style": "软萌可爱", "desc": "适合童话故事、角色扮演"},
    {"id": "zh-CN-XiaomoNeural", "name": "晓墨", "gender": "女",
     "style": "知性从容", "desc": "适合知识类口播、说明文档"},
    {"id": "zh-CN-XiaoxuanNeural", "name": "晓萱", "gender": "女",
     "style": "清晰利落", "desc": "适合新闻资讯、播报"},
    {"id": "zh-CN-YunxiNeural", "name": "云希", "gender": "男",
     "style": "清晰自然", "desc": "通用男声，适合解说、教程"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬", "gender": "男",
     "style": "专业播报", "desc": "新闻播报、正式文稿"},
    {"id": "zh-CN-YunjianNeural", "name": "云健", "gender": "男",
     "style": "浑厚有力", "desc": "宣传片、体育解说"},
    {"id": "zh-CN-YunyeNeural", "name": "云野", "gender": "男",
     "style": "沉稳叙事", "desc": "有声书、纪录片旁白"},
    {"id": "zh-CN-YunxiaNeural", "name": "云夏", "gender": "男",
     "style": "少年清澈", "desc": "角色配音、动漫台词"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北", "gender": "女",
     "style": "东北方言", "desc": "方言趣味内容"},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "name": "晓妮", "gender": "女",
     "style": "陕西方言", "desc": "方言趣味内容"},
]

# 在线音色列表中需要过滤出的语种前缀
VOICE_LANG_PREFIXES = ("zh-CN", "zh-HK", "zh-TW")
