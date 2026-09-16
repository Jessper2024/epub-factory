#!/usr/bin/env python3
"""成书看板：常驻本地服务，随时打开都能看到项目当前的运行情况。

只监听 127.0.0.1，数据不出本机；页面每 60 秒自动刷新，扫描结果缓存 30 秒，
所以开着不费资源，需要最新数据时点页面上的「立即刷新」即可。

    ./epub.sh dash          # 起服务（已在跑就只打开浏览器）
    ./.venv/bin/python dashboard.py --port 8760 --no-open
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))
import build_epub as B  # noqa: E402
import report as RP  # noqa: E402

DEFAULT_PORT = 8760
CACHE_TTL = 30          # 秒；同一份扫描结果复用这么久
PAGE_REFRESH = 60       # 秒；页面自动重新加载的间隔
# 这些文件改了就让看板自己热重载，不用重启（长期项目会一直改规则）
WATCHED = ("promo.py", "build_epub.py", "report.py")

_cache: dict = {"ts": 0.0, "html": ""}
_seen_mtime: dict = {}
_lock = threading.Lock()


def _hot_reload() -> None:
    """监视脚本改动：变了就重载，页面立刻反映新规则。"""
    global B, RP
    stale = False
    for name in WATCHED:
        p = ENGINE / name
        if not p.exists():
            continue
        m = p.stat().st_mtime
        if name in _seen_mtime and _seen_mtime[name] != m:
            stale = True
        _seen_mtime[name] = m
    if not stale:
        return
    try:
        import promo as P
        importlib.reload(P)
        B = importlib.reload(B)
        RP = importlib.reload(RP)
        print("[看板] 检测到脚本改动，已热重载 %s" % "、".join(WATCHED), flush=True)
    except Exception as exc:  # noqa: BLE001
        print("[看板] 热重载失败（继续用旧代码）：%s" % exc, flush=True)


def render(force: bool = False) -> str:
    _hot_reload()
    now = time.time()
    with _lock:
        if not force and _cache["html"] and now - _cache["ts"] < CACHE_TTL:
            return _cache["html"]
    accounts = [RP.scan_account(b) for b in B.collect_book_dirs()]
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = RP.build_html(accounts, stamp, live=True, refresh_sec=PAGE_REFRESH)
    with _lock:
        _cache.update(ts=now, html=html)
    return html


class Handler(BaseHTTPRequestHandler):
    server_version = "EpubDash/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            body = render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/refresh"):
            render(force=True)
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send_error(404, "not found")

    def log_message(self, *args) -> None:  # 静音，别刷日志
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="成书看板本地服务")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    port = args.port
    for _ in range(10):          # 端口被占就往后找
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        print("找不到可用端口")
        return 1

    url = "http://127.0.0.1:%d/" % port
    print("成书看板已启动：%s" % url)
    print("数据只在本机，页面每 %d 秒自动刷新。Ctrl+C 停止。" % PAGE_REFRESH)
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
