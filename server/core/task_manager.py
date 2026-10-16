# -*- coding: utf-8 -*-
"""异步合成任务管理器。

长文本（小说、讲稿）合成耗时从十几秒到数分钟不等，HTTP 请求不可能同步等待，
因此采用"提交任务 + 轮询状态"的模型：

    提交 → 后台线程池按段合成 → 前端按 task_id 轮询进度 → 取回音频地址

设计要点：
- 任务状态保存在内存字典，带读写锁，避免多线程竞态；
- 每个任务在独立线程内创建自己的事件循环，edge-tts 的异步 API 得以串行推进；
- 进度回调覆盖"分段合成"阶段（0~95%），合并与落盘占最后 5%；
- 守护线程定期回收过期任务与音频文件，防止磁盘无限增长。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from config import AUDIO_DIR, Config
from core.tts_engine import TTSEngine

logger = logging.getLogger("qingyu.task")

PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"


class Task:
    """单个合成任务的状态载体。"""

    def __init__(self, text: str, voice: str, rate: int, volume: int) -> None:
        self.id: str = uuid.uuid4().hex
        self.status: str = PENDING
        self.progress: int = 0
        self.done_segments: int = 0
        self.total_segments: int = 0
        self.chars: int = len(text)
        self.voice: str = voice
        self.rate: int = rate
        self.volume: int = volume
        self.audio_url: Optional[str] = None
        self.filename: Optional[str] = None
        self.duration: float = 0.0
        self.elapsed: float = 0.0
        self.merge_mode: str = ""
        self.ssml: bool = True
        self.ext: str = "mp3"
        self.engine: str = "edge"
        self.error: Optional[str] = None
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self._text = text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.id,
            "status": self.status,
            "progress": self.progress,
            "done_segments": self.done_segments,
            "total_segments": self.total_segments,
            "chars": self.chars,
            "voice": self.voice,
            "rate": self.rate,
            "volume": self.volume,
            "audio_url": self.audio_url,
            "duration": self.duration,
            "elapsed": self.elapsed,
            "merge_mode": self.merge_mode,
            "ssml": self.ssml,
            "ext": self.ext,
            "engine": self.engine,
            "error": self.error,
            "updated_at": self.updated_at,
        }


class TaskManager:
    """任务的创建、执行、查询与回收。"""

    def __init__(self, engine: TTSEngine) -> None:
        self.engine = engine
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=Config.MAX_WORKERS, thread_name_prefix="qingyu-synth"
        )
        self._stop = threading.Event()
        self._cleaner = threading.Thread(target=self._cleanup_loop, daemon=True, name="qingyu-cleaner")
        self._cleaner.start()

    # ------------------------------------------------------------ 生命周期

    def create(self, text: str, voice: str, rate: int, volume: int) -> Task:
        task = Task(text, voice, rate, volume)
        with self._lock:
            self._tasks[task.id] = task
        self._executor.submit(self._run, task)
        logger.info("任务 %s 已创建，字数 %s，音色 %s", task.id, task.chars, voice)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def shutdown(self) -> None:
        self._stop.set()
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------ 执行

    def _run(self, task: Task) -> None:
        task.status = RUNNING
        task.updated_at = time.time()
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)

            def on_progress(done: int, total: int) -> None:
                task.done_segments = done
                task.total_segments = total
                task.progress = int(done * 95 / max(total, 1))
                task.updated_at = time.time()

            data, stats = loop.run_until_complete(
                self.engine.synth_text(
                    task._text,
                    task.voice,
                    rate=task.rate,
                    volume=task.volume,
                    progress_cb=on_progress,
                )
            )

            ext = stats.get("ext", "mp3")
            if ext not in ("mp3", "wav"):
                ext = "mp3"
            filename = f"{task.id}.{ext}"
            target = Path(AUDIO_DIR) / filename
            target.write_bytes(data)

            task.filename = filename
            task.audio_url = f"/api/audio/{filename}"
            task.duration = stats.get("duration", 0.0)
            task.elapsed = stats.get("elapsed", 0.0)
            task.chars = stats.get("chars", task.chars)
            task.total_segments = stats.get("segments", task.total_segments)
            task.ssml = bool(stats.get("ssml", True))
            task.ext = ext
            task.engine = stats.get("engine", "edge")
            task.progress = 100
            task.status = DONE
            logger.info(
                "任务 %s 完成：%s 段 / %s 字 / %s 秒音频 / 耗时 %ss",
                task.id, task.total_segments, task.chars, task.duration, task.elapsed,
            )
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落到任务状态里
            task.status = ERROR
            task.error = str(exc)[:300]
            logger.error("任务 %s 失败：%s", task.id, exc)
        finally:
            task.updated_at = time.time()
            try:
                loop.close()
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------ 回收

    def _cleanup_loop(self) -> None:
        while not self._stop.wait(120):
            try:
                self._cleanup_once()
            except Exception as exc:  # pragma: no cover
                logger.warning("清理线程异常：%s", exc)

    def _cleanup_once(self) -> None:
        now = time.time()
        audio_dir = Path(AUDIO_DIR)
        removed_files = 0
        for path in audio_dir.glob("*.mp3"):
            try:
                if now - path.stat().st_mtime > Config.AUDIO_TTL_SECONDS:
                    path.unlink()
                    removed_files += 1
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("删除音频失败 %s：%s", path.name, exc)

        with self._lock:
            expired = [
                tid for tid, t in self._tasks.items()
                if now - t.updated_at > Config.TASK_TTL_SECONDS
            ]
            for tid in expired:
                self._tasks.pop(tid, None)

        if removed_files or expired:
            logger.info("清理完成：删除 %s 个音频，回收 %s 个任务状态", removed_files, len(expired))
