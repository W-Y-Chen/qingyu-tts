# -*- coding: utf-8 -*-
"""接口层离线测试：用假的合成引擎替换真实网络调用，验证完整 HTTP 流程。"""

import time

import pytest

from app import create_app
from config import Config, FALLBACK_VOICES


FAKE_MP3 = b"ID3\x03" + b"\x00" * 512


@pytest.fixture()
def client(monkeypatch):
    app = create_app()
    app.config["TESTING"] = True
    engine = app.extensions["qingyu"]["engine"]

    async def fake_fetch_voices():
        raise RuntimeError("离线测试：不访问网络")

    async def fake_synth_text(text, voice, rate=0, volume=0, max_chars=None, progress_cb=None):
        if progress_cb:
            progress_cb(1, 1)
        return FAKE_MP3, {
            "chars": len(text),
            "segments": 1,
            "pause_count": 0,
            "elapsed": 0.01,
            "bytes": len(FAKE_MP3),
            "duration": 0.1,
            "ssml": True,
        }

    monkeypatch.setattr(type(engine), "fetch_voices", staticmethod(fake_fetch_voices))
    monkeypatch.setattr(engine, "synth_text", fake_synth_text)
    with app.test_client() as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["voices"] == len(FALLBACK_VOICES)
    assert "ssml_pause" in body


def test_voices_fallback(client):
    resp = client.get("/api/voices")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["source"] == "fallback"
    assert body["count"] == len(FALLBACK_VOICES)
    ids = [v["id"] for v in body["voices"]]
    assert "zh-CN-XiaoxiaoNeural" in ids


def make_preview(client, **extra):
    """非测试的辅助函数：发起一次试听并返回响应体。"""
    payload = {
        "text": "轻语，是一款专注文本转语音的小程序。",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": 0,
        "volume": 0,
    }
    payload.update(extra)
    return client.post("/api/preview", json=payload)


def test_preview_success(client):
    resp = make_preview(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["audio_url"].startswith("/api/audio/")
    assert body["audio_url"].endswith(".mp3")


def test_preview_rejects_empty_text(client):
    resp = client.post("/api/preview", json={"text": "   "})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_preview_rejects_unknown_voice(client):
    resp = client.post("/api/preview", json={"text": "你好", "voice": "not-a-voice"})
    assert resp.status_code == 400


def test_preview_clamps_rate_and_volume(client):
    resp = client.post("/api/preview", json={
        "text": "你好", "voice": "zh-CN-YunxiNeural", "rate": 999, "volume": -999,
    })
    assert resp.status_code == 200


def test_preview_truncates_long_text(client):
    resp = client.post("/api/preview", json={"text": "啊" * 5000})
    assert resp.status_code == 200
    assert resp.get_json()["clipped"] is True


def test_synthesize_and_poll(client):
    created = client.post("/api/synthesize", json={
        "text": "第一段。\n\n第二段。", "voice": "zh-CN-XiaoxiaoNeural",
    })
    assert created.status_code == 200
    task_id = created.get_json()["task_id"]

    body = None
    for _ in range(100):  # 后台线程需要被调度，轮询需让出时间片
        body = client.get(f"/api/tasks/{task_id}").get_json()
        if body["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert body["status"] == "done", body
    assert body["progress"] == 100
    assert body["audio_url"].endswith(".mp3")

    audio = client.get(body["audio_url"])
    assert audio.status_code == 200
    assert audio.data == FAKE_MP3
    assert audio.headers["Accept-Ranges"] == "bytes"


def test_task_not_found(client):
    resp = client.get("/api/tasks/deadbeef")
    assert resp.status_code == 404
    assert resp.get_json()["error"]


def test_audio_rejects_unsafe_filename(client):
    assert client.get("/api/audio/..%2f..%2fsecret.txt").status_code in (400, 404)
    assert client.get("/api/audio/evil.mp3").status_code == 400


def test_audio_range_request(client):
    body = make_preview(client).get_json()
    resp = client.get(body["audio_url"], headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert len(resp.data) == 100
    assert "bytes 0-99/" in resp.headers["Content-Range"]


def test_web_index_is_served(client):
    """网页版由后端同源托管，访问根路径应返回页面。"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "轻语 TTS" in resp.data.decode("utf-8")


def test_web_static_whitelist(client):
    """只暴露网页版自身的资源文件，其它一律 404。"""
    assert client.get("/web/app.js").status_code == 200
    assert client.get("/web/style.css").status_code == 200
    assert client.get("/web/config.py").status_code == 404
    assert client.get("/web/../config.py").status_code == 404


def test_max_text_chars_enforced(client):
    resp = client.post("/api/synthesize", json={"text": "啊" * (Config.MAX_TEXT_CHARS + 1)})
    assert resp.status_code == 400
