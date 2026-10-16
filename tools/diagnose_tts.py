# -*- coding: utf-8 -*-
"""语音合成故障诊断脚本。

当页面提示 "No audio was received" 或类似错误时运行本脚本，
它会逐层排查从本机到微软合成服务的链路，并给出结论建议。

运行（用与服务端相同的 Python 解释器）：
    cd E:\\大学学习\\项目\\轻语\\server
    python ..\\tools\\diagnose_tts.py

把完整输出复制回给开发者即可定位问题。
"""

from __future__ import annotations

import asyncio
import datetime
import os
import platform
import socket
import sys
import time
import traceback

HOST = "speech.platform.bing.com"
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "NO_PROXY")


def section(title: str) -> None:
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


def main() -> int:
    verdict = []

    # ---------------------------------------------------------- 1 环境
    section("1. 运行环境")
    print("Python 版本 :", sys.version.replace("\n", " "))
    print("操作系统    :", platform.platform())
    print("解释器路径  :", sys.executable)

    try:
        from importlib.metadata import version
        print("edge-tts    :", version("edge-tts"))
    except Exception:
        try:
            import edge_tts
            print("edge-tts    :", getattr(edge_tts, "__version__", "未知"))
        except Exception as exc:
            print("edge-tts    : 导入失败 ->", exc)
            verdict.append("edge-tts 未安装或无法导入，请先执行 pip install -r requirements.txt")

    # ---------------------------------------------------------- 2 时间
    section("2. 系统时间（edge-tts 鉴权依赖时间戳，偏差过大会被拒）")
    print("本地时间 :", datetime.datetime.now())
    print("UTC 时间 :", datetime.datetime.utcnow())
    try:
        import urllib.request
        req = urllib.request.Request("https://www.baidu.com", method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as resp:
            server_date = resp.headers.get("Date")
        print("网络时间 :", server_date, "（来自 baidu.com 的 Date 头）")
        if server_date:
            from email.utils import parsedate_to_datetime
            net = parsedate_to_datetime(server_date).replace(tzinfo=None)
            drift = abs((datetime.datetime.utcnow() - net).total_seconds())
            print(f"时间偏差 : {drift:.0f} 秒")
            if drift > 300:
                verdict.append(f"系统时间与网络时间相差 {drift:.0f} 秒，超过 5 分钟会导致鉴权失败，请校准系统时间")
    except Exception as exc:
        print("网络时间 : 获取失败 ->", exc)

    # ---------------------------------------------------------- 3 DNS
    section(f"3. DNS 解析 {HOST}")
    dns_ok = False
    try:
        infos = socket.getaddrinfo(HOST, 443)
        ips = sorted({i[4][0] for i in infos})
        print("解析结果 :", ", ".join(ips))
        dns_ok = True
    except Exception as exc:
        print("解析失败 :", exc)
        verdict.append("域名解析失败，可能是 DNS 污染或网络受限")

    # ---------------------------------------------------------- 4 TCP
    section("4. 网络连通性（TCP 443）")
    tcp_ok = False
    for host in (HOST, "www.bing.com", "www.baidu.com"):
        try:
            t0 = time.time()
            sock = socket.create_connection((host, 443), timeout=8)
            sock.close()
            cost = time.time() - t0
            print(f"{host:32s} 可连接  {cost:.2f}s")
            if host == HOST:
                tcp_ok = True
        except Exception as exc:
            print(f"{host:32s} 失败    {type(exc).__name__}: {exc}")

    if not tcp_ok and dns_ok:
        verdict.append(
            f"能解析 {HOST} 但连不上 443 端口：这是典型的网络层阻断。"
            "该域名在部分国内网络下不可达，需更换网络或配置代理。"
        )

    # ---------------------------------------------------------- 5 代理
    section("5. 代理设置")
    found = {k: os.environ[k] for k in PROXY_KEYS if os.environ.get(k)}
    if found:
        for k, v in found.items():
            print(f"{k} = {v}")
    else:
        print("未检测到代理环境变量")

    # ---------------------------------------------------------- 6 实际合成
    section("6. 实际合成测试（真实调用微软服务）")
    ok = False
    try:
        import edge_tts

        async def run() -> int:
            comm = edge_tts.Communicate("轻语测试。", "zh-CN-XiaoxiaoNeural")
            total = 0
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    total += len(chunk["data"])
            return total

        t0 = time.time()
        size = asyncio.run(run())
        print(f"成功：收到 {size} 字节音频，耗时 {time.time() - t0:.2f}s")
        ok = size > 0
        if not ok:
            verdict.append("接口返回成功但没有音频数据")
    except Exception as exc:
        print("失败：", type(exc).__name__, "-", exc)
        print("\n--- 完整异常栈 ---")
        traceback.print_exc()

    # ---------------------------------------------------------- 7 结论
    section("7. 结论与建议")
    if ok:
        print("[正常] 本机到微软合成服务的链路可用。")
        print("       如果页面上仍失败，请检查服务端的网络出口（是否走了代理）、")
        print("       以及服务是否以同一账号/网络运行。")
    else:
        if not verdict:
            verdict.append("合成调用失败，但网络层检查未发现明显问题，请把上面的异常栈发给开发者")
        for i, item in enumerate(verdict, 1):
            print(f"[{i}] {item}")
        print("\n可选处置：")
        print("  · 更换网络环境（如切到手机热点）后重跑本脚本对比")
        print("  · 若必须走代理，可在 server/config.py 中为 edge-tts 传入 proxy 参数")
        print("  · 若网络长期不可达，建议改用国内商用语音合成服务")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
