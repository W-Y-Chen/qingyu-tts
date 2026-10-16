# -*- coding: utf-8 -*-
"""轻语 TTS 服务端入口。

启动方式：
    python app.py
或（生产）：
    waitress-serve --host=0.0.0.0 --port=5000 app:app

说明：Flask 自带的开发服务器仅供调试；正式部署请使用 WSGI 服务器
（gunicorn / waitress）并置于 HTTPS 反向代理之后，
微信小程序生产环境强制要求 HTTPS 域名。
"""

from __future__ import annotations

import logging

from pathlib import Path

from flask import Flask, abort, send_from_directory
from flask_cors import CORS

from api.routes import register_routes
from config import Config
from core.task_manager import TaskManager
from core.tts_engine import TTSEngine

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# 网页版前端目录（与 server/ 同级）。由本服务直接托管，实现同源部署：
# 手机与电脑浏览器访问同一个地址即可使用，无需处理跨域。
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
WEB_ASSETS = {"app.js", "style.css"}
# 由 tools/build_standalone.py 生成的单文件版（样式与脚本已内联，便于传到手机打开）
MOBILE_PAGE = "轻语TTS-移动端.html"


def create_app() -> Flask:
    Config.ensure_dirs()

    app = Flask(__name__)
    # send_wildcard=True：当允许的来源为 * 时，始终返回 Access-Control-Allow-Origin: *
    # 而不是回显请求来源。这样以 file:// 打开的页面（opaque origin，Origin 头为 null）
    # 也能正常跨域调用接口——把单文件 HTML 传到手机上直接打开的场景依赖这一点。
    CORS(
        app,
        resources={r"/api/*": {"origins": Config.CORS_ORIGINS}},
        supports_credentials=False,
        send_wildcard=True,
    )

    engine = TTSEngine()
    tasks = TaskManager(engine)
    register_routes(app, engine, tasks)

    # ---- 网页版静态站点（同源） ----
    @app.get("/")
    def web_index():
        if not (WEB_DIR / "index.html").exists():
            abort(404)
        return send_from_directory(str(WEB_DIR), "index.html")

    @app.get("/web/<path:filename>")
    def web_static(filename: str):
        # 白名单：只提供网页版自身的资源文件
        if filename not in WEB_ASSETS:
            abort(404)
        return send_from_directory(str(WEB_DIR), filename)

    @app.get("/style.css")
    def web_css():
        # 根级别名：页面内使用相对路径引用，这样双击 index.html 打开也能正确加载样式
        return send_from_directory(str(WEB_DIR), "style.css")

    @app.get("/app.js")
    def web_js():
        return send_from_directory(str(WEB_DIR), "app.js")

    @app.get("/standalone.html")
    def web_standalone():
        target = WEB_DIR / "standalone.html"
        if not target.exists():
            abort(404)
        return send_from_directory(str(WEB_DIR), "standalone.html")

    @app.get("/m")
    def web_mobile():
        """短地址：直接提供单文件版，便于在手机浏览器里少输字符。"""
        if not (WEB_DIR / MOBILE_PAGE).exists():
            abort(404)
        return send_from_directory(str(WEB_DIR), MOBILE_PAGE)

    app.extensions["qingyu"] = {"engine": engine, "tasks": tasks}
    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.getLogger().info(
        "轻语 TTS 服务启动：http://%s:%s （音色 %s，调试模式 %s）",
        Config.HOST, Config.PORT, Config.DEFAULT_VOICE, Config.DEBUG,
    )
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG, threaded=True)
