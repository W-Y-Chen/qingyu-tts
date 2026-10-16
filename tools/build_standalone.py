# -*- coding: utf-8 -*-
"""把 web/ 下的分离文件打包为自包含的单文件 HTML。

用途：把样式与脚本内联进 HTML，得到一个零外部依赖的文件。适用于：
- 传到手机上直接点开使用（配合 --host 预置后端地址）；
- 发给别人、投屏演示、上传到只接受单个 HTML 的平台。

源文件仍是 web/index.html + style.css + app.js，本脚本只做产物生成。

用法：
    python tools/build_standalone.py                          # 生成 web/standalone.html
    python tools/build_standalone.py --host 192.168.43.4      # 生成 web/轻语TTS-移动端.html
    python tools/build_standalone.py --host 1.2.3.4 --port 8000 --out dist/a.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

CSS_TAG = '<link rel="stylesheet" href="style.css">'
JS_TAG = '<script src="app.js"></script>'
HEAD_MARK = "<title>"


def build(host: str | None, port: int, out: Path) -> int:
    index = WEB_DIR / "index.html"
    css = WEB_DIR / "style.css"
    js = WEB_DIR / "app.js"

    for f in (index, css, js):
        if not f.exists():
            print(f"[错误] 缺少源文件：{f}", file=sys.stderr)
            return 1

    html = index.read_text(encoding="utf-8")
    for tag in (CSS_TAG, JS_TAG):
        if tag not in html:
            print(f"[错误] index.html 中未找到 {tag}，请检查后更新本脚本。", file=sys.stderr)
            return 1

    css_text = css.read_text(encoding="utf-8")
    js_text = js.read_text(encoding="utf-8")

    # 内联前的安全检查：内容不得提前闭合标签
    if "</style" in css_text.lower():
        print("[错误] CSS 中含有 </style 字样，无法安全内联。", file=sys.stderr)
        return 1
    if "</script" in js_text.lower():
        print("[错误] JS 中含有 </script 字样，无法安全内联。", file=sys.stderr)
        return 1

    html = html.replace(CSS_TAG, "<style>\n" + css_text + "\n</style>")
    html = html.replace(JS_TAG, "<script>\n" + js_text + "\n</script>")

    if host:
        api = f"http://{host}:{port}"
        inject = f'<script>window.QINGYU_DEFAULT_API="{api}";</script>\n    '
        if HEAD_MARK not in html:
            print("[错误] 未找到注入位置（<title>）。", file=sys.stderr)
            return 1
        html = html.replace(HEAD_MARK, inject + HEAD_MARK, 1)
        print(f"[信息] 已预置后端地址：{api}")
    else:
        print("[信息] 未预置后端地址：页面会提示或让用户手动填写")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    print(f"[完成] 已生成 {out}  （{out.stat().st_size / 1024:.1f} KB）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="打包轻语 TTS 单文件网页版")
    p.add_argument("--host", help="预置的后端主机 IP / 域名（手机访问时填电脑局域网 IP）")
    p.add_argument("--port", type=int, default=5000, help="后端端口，默认 5000")
    p.add_argument("--out", help="输出文件路径，默认 web/standalone.html")
    args = p.parse_args()

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = WEB_DIR.parent / out
    elif args.host:
        out = WEB_DIR / "轻语TTS-移动端.html"
    else:
        out = WEB_DIR / "standalone.html"

    return build(args.host, args.port, out)


if __name__ == "__main__":
    raise SystemExit(main())
