#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投递箱实时进度页：只监听本机，1 秒轮询 `_进度.json`，不动文件。

    python3 progress_server.py           起服务（默认 8770，自动开浏览器）
    python3 progress_server.py --port 8800 --no-open

为什么起本地服务而不是直接开个 HTML 文件
    浏览器对 `file://` 的 fetch/XHR 限制很严（CORS 直接拦），
    读不到 `_进度.json`。起个只监听 127.0.0.1 的小服务最稳，
    数据一分都不出网。

为什么 1 秒轮询而不是 WebSocket
    进度来源就是一个 JSON 文件，1 秒轮询完全够（处理一个文件要几秒），
    而且**任何一个进程崩了页面都能活**——WebSocket 那条长连接断了要重连逻辑，
    为了省这点开销不值得。
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parent
PROGRESS_FILE = ROOT / "_进度.json"

PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>投递箱处理进度</title>
<style>
  :root { --bg:#FBFAF7; --card:#fff; --line:#E8E5DE; --ink:#2A2723; --muted:#8A857C;
          --ok:#3B7A57; --run:#B07A1E; --wait:#B9B4AA; --warn:#A8541C; --fail:#A03030; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif; }
  .wrap { max-width:860px; margin:0 auto; padding:32px 24px 64px; }
  h1 { font-size:20px; font-weight:600; margin:0 0 4px; letter-spacing:.02em; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:22px; }
  .bar { height:8px; background:#EDEAE3; border-radius:99px; overflow:hidden; margin:18px 0 6px; }
  .bar > i { display:block; height:100%; width:0; background:var(--ok);
             border-radius:99px; transition:width .35s ease; }
  .counts { display:flex; gap:16px; flex-wrap:wrap; color:var(--muted);
            font-size:13px; margin-bottom:20px; }
  .counts b { color:var(--ink); font-weight:600; }
  ul { list-style:none; margin:0; padding:0; }
  li { display:flex; align-items:flex-start; gap:10px; padding:11px 14px;
       background:var(--card); border:1px solid var(--line); border-radius:10px;
       margin-bottom:7px; font-size:13.5px; }
  li .ic { flex:0 0 20px; text-align:center; line-height:1.5; }
  li .nm { flex:1; min-width:0; word-break:break-all; line-height:1.5; }
  li .tg { flex:0 0 auto; color:var(--muted); font-size:12px; margin-left:10px;
           max-width:240px; text-align:right; line-height:1.5; }
  li.ok      { border-color:#D6E6DA; }
  li.ok .ic  { color:var(--ok); }
  li.running { border-color:#EBD9B6; background:#FFFCF4; }
  li.running .ic { color:var(--run); }
  li.dup .ic  { color:var(--muted); }
  li.unknown { border-color:#F0DCC4; background:#FFFBF5; }
  li.unknown .ic { color:var(--warn); }
  li.fail    { border-color:#EFD2D2; }
  li.fail .ic { color:var(--fail); }
  li.waiting .ic { color:var(--wait); }
  .done { background:#EAF3EC; border:1px solid #D6E6DA; color:var(--ok);
          border-radius:10px; padding:12px 16px; font-size:13.5px; margin-bottom:18px; }
  .links { margin-top:26px; padding-top:18px; border-top:1px solid var(--line);
           color:var(--muted); font-size:12.5px; line-height:2; }
  .links a { color:#2F6EAF; text-decoration:none; }
  .links a:hover { text-decoration:underline; }
  .spin { display:inline-block; animation:sp 1.1s linear infinite; }
  @keyframes sp { to { transform:rotate(360deg); } }
</style></head><body><div class="wrap">
  <h1>投递箱处理进度</h1>
  <div class="sub" id="sub">连接中…</div>
  <div class="bar"><i id="bar"></i></div>
  <div class="counts" id="counts"></div>
  <div id="done"></div>
  <ul id="list"></ul>
  <div id="booksBox" style="display:none;">
    <div class="sub" style="margin:24px 0 10px;font-weight:600;color:var(--ink);">更新 epub</div>
    <ul id="books"></ul>
  </div>
  <div class="links">
    完整日志：<a href="/open/log" target="_blank">_投递箱处理日志.md</a><br>
    处理报告：<a href="/open/report" target="_blank">_处理报告.md</a><br>
    定稿库：<a href="/open/lib" target="_blank">02、定稿库/</a>
  </div>
</div>
<script>
const ICON = { ok:'✅', running:'⏳', waiting:'⬜', dup:'⏭', unknown:'⚠️', fail:'❌' };
const LABEL = { ok:'已归档', running:'处理中', waiting:'等待', dup:'重复跳过',
                unknown:'待判', fail:'失败' };
let lastCount = 0;

function render(d) {
  const items = d.items || [];
  const n = items.length;
  const by = k => items.filter(x => x.state === k).length;
  const okN = by('ok'), dupN = by('dup'), unkN = by('unknown'), failN = by('fail');
  const doneN = okN + dupN + unkN + failN;
  const pct = n ? Math.round(doneN / n * 100) : 0;

  document.getElementById('bar').style.width = pct + '%';
  document.getElementById('sub').textContent = d.finished
      ? `已完成　${fmt(d.updated_at)}`
      : `处理中　开始于 ${fmt(d.started_at)}　·　页面每秒自动刷新`;

  document.getElementById('counts').innerHTML = [
    `全部 <b>${n}</b>`, `已归档 <b>${okN}</b>`, `重复 <b>${dupN}</b>`,
    unkN ? `待判 <b>${unkN}</b>` : '', failN ? `失败 <b>${failN}</b>` : '',
    `等待 <b>${by('waiting')}</b>`
  ].filter(Boolean).join('');

  document.getElementById('done').innerHTML = d.finished
      ? `<div class="done">✓ 处理完成　${d.note || ''}</div>` : '';

  document.getElementById('list').innerHTML = items.map(x => {
    const ic = x.state === 'running'
        ? `<span class="spin">⏳</span>` : (ICON[x.state] || '⬜');
    const tg = x.target ? `<span class="tg">${esc(x.target)}</span>`
               : (x.detail ? `<span class="tg">${esc(x.detail)}</span>` : '');
    return `<li class="${x.state}"><span class="ic">${ic}</span>` +
           `<span class="nm">${esc(x.name)}</span>${tg}</li>`;
  }).join('');

  // 更新 epub 阶段（陈少 2026-10-14：处理投递箱后顺带更新的书）
  const books = d.books || [];
  const booksBox = document.getElementById('booksBox');
  if (books.length) {
    booksBox.style.display = 'block';
    document.getElementById('books').innerHTML = books.map(b => {
      const ic = b.state === 'fail' ? '❌' : '📖';
      const tg = b.detail ? `<span class="tg">${esc(b.detail)}</span>` : '';
      return `<li class="${b.state === 'fail' ? 'fail' : 'ok'}"><span class="ic">${ic}</span>` +
             `<span class="nm">${esc(b.name)}</span>${tg}</li>`;
    }).join('');
  }

  // 归档数变化时给点反馈（页面在后台也能看到标题闪一下）
  if (okN !== lastCount) {
    lastCount = okN;
    document.title = `投递箱处理进度（${doneN}/${n}）`;
  }
}
function esc(s) {
  return String(s || '').replace(/[&<>"]/g, c =>
      ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
}
function fmt(t) {
  return t ? String(t).replace('T', ' ').slice(5, 16) : '—';
}
async function poll() {
  try {
    const r = await fetch('/progress.json?t=' + Date.now(), { cache: 'no-store' });
    if (r.ok) render(await r.json());
  } catch (e) { /* 服务还在起 / 暂时读不到，下一轮再试 */ }
}
poll();
setInterval(poll, 1000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):        # 静音：别把访问日志刷进终端
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                 # noqa: N802
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/progress.json":
            try:
                data = PROGRESS_FILE.read_bytes()
            except OSError:
                data = json.dumps({"items": [], "finished": False,
                                   "title": "等待开始"}).encode("utf-8")
            # 文件可能正在被原子替换，读到空就返回空壳，页面下一轮再取
            if not data.strip():
                data = b'{"items":[],"finished":false}'
            self._send(data, "application/json; charset=utf-8")
            return

        if path.startswith("/open/"):
            # 只放行三个白名单文件，别变成任意文件读取的口子
            targets = {
                "/open/log": ROOT / "_投递箱处理日志.md",
                "/open/report": ROOT / "_处理报告.md",
            }
            if path in targets:
                try:
                    self._send(targets[path].read_bytes(),
                               "text/plain; charset=utf-8")
                except OSError:
                    self._send(b"(file not found)", "text/plain; charset=utf-8", 404)
                return
            if path == "/open/lib":
                # 列出定稿库结构，便于直接定位产物
                lines = []
                lib = ROOT / "02、定稿库"
                if lib.is_dir():
                    for p in sorted(lib.iterdir()):
                        if p.is_dir() and not p.name.startswith("."):
                            lines.append("[%s]" % p.name)
                            for a in sorted(p.iterdir()):
                                if a.is_dir() and not a.name.startswith("."):
                                    lines.append("    %s" % a.name)
                self._send("\n".join(lines).encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            self._send(b"(not allowed)", "text/plain; charset=utf-8", 403)
            return

        self._send(b"404", "text/plain; charset=utf-8", 404)


def main() -> int:
    ap = argparse.ArgumentParser(description="投递箱实时进度页")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true", help="不自动开浏览器")
    args = ap.parse_args()

    # 端口被占就往后试几个，别让「上一次没退干净」把这次挡住
    srv = None
    for port in range(args.port, args.port + 8):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)   # 只监听本机
            break
        except OSError:
            continue
    if srv is None:
        print("端口 %d-%d 都被占用，起不来。" % (args.port, args.port + 7))
        return 1

    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("进度页：%s" % url)
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
