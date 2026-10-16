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
PROGRESS_FILE = ROOT / "_管理" / "_进度.json"

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
  .toolbar { margin:12px 0 4px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .toolbar button { font:inherit; font-size:13px; padding:7px 16px; border:1px solid var(--fail);
                    background:#fff; color:var(--fail); border-radius:8px; cursor:pointer; }
  .toolbar button:hover { background:var(--fail); color:#fff; }
  .toolbar button:disabled { opacity:.5; cursor:default; }
  .tb-hint { color:var(--muted); font-size:12px; }
  .sec { margin-top:26px; }
  .sec-h { font-size:14px; font-weight:600; color:var(--ink);
           padding-bottom:8px; border-bottom:1px solid var(--line);
           display:flex; align-items:center; justify-content:space-between; }
  .sec-c { font-size:12px; font-weight:400; color:var(--muted); }
  .empty { color:var(--muted); font-size:12.5px; padding:8px 2px; }
  .overview { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
              gap:10px; margin-bottom:22px; }
  .ov { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:10px 12px; }
  .ov-t { font-size:12px; color:var(--muted); margin-bottom:4px; }
</style></head><body><div class="wrap">
  <h1>项目处理进度</h1>
  <div class="sub" id="sub">连接中…</div>
  <div class="toolbar" id="toolbar" style="display:none;">
    <button id="btn-clear" type="button">跳过卡死任务</button>
    <span class="tb-hint">把「处理中/失败」卡住的任务关掉，进度条继续往下走；running 下轮 command 重试，fail 源料丢废纸篓</span>
  </div>
  <div class="bar"><i id="bar"></i></div>
  <div class="counts" id="counts"></div>
  <div id="done"></div>
  <div class="overview" id="overview"></div>
  <div id="sections"></div>
  <div class="links">
    完整日志：<a href="/open/log" target="_blank">_投递箱处理日志.md</a><br>
    处理报告：<a href="/open/report" target="_blank">_处理报告.md</a><br>
    定稿库：<a href="/open/lib" target="_blank">02、定稿库/</a>
  </div>
</div>
<script>
const ICON = { ok:'✅', running:'⏳', waiting:'⬜', dup:'⏭', unknown:'⚠️', fail:'❌', skip:'⏭', done:'✅' };
const LABEL = { ok:'已归档', running:'处理中', waiting:'等待', dup:'重复跳过',
                unknown:'待判', fail:'失败', skip:'跳过', done:'完成' };
let lastCount = 0;
let clearing = false;

async function clearStuck() {
  if (clearing) return;
  clearing = true;
  renderLast();
  try {
    const r = await fetch('/clear', { method: 'POST' });
    const d = await r.json();
    // 后端清完，等下一轮 poll 自动刷新（renderLast 里已把按钮置灰）
  } catch (e) { /* 忽略，下一轮 poll 会恢复正常 */ }
  clearing = false;
}

// 缓存最近一次 render 用的数据，方便按钮点击时立即重绘（置灰）
let lastData = null;
function renderLast() {
  if (lastData) render(lastData);
}

function fmt(t) {
  return t ? String(t).replace('T', ' ').slice(5, 16) : '—';
}
function esc(s) {
  return String(s || '').replace(/[&<>"]/g, c =>
      ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
}

// 板块定义（与 progress.py SECTIONS 对齐：key, 标题, 产出说明）
const SEC_ORDER = [
  ['proofread', '文稿精校', '视频线二次精校'],
  ['video',   '视频文稿', '字幕 / 精校稿'],
  ['notes',   '读书笔记', '笔记 xhtml'],
  ['article', '文章合集', '公众号 epub'],
  ['book',    '出版图书', '完整书 epub'],
  ['epub',    'EPUB 拆解', '分章 xhtml'],
  ['adult',   '成人作品集', '演员 epub'],
  ['photo',   '写真集', '图集 epub'],
  ['finish',  '系统', ''],
];

function itemHtml(x) {
  const ic = x.state === 'running'
      ? `<span class="spin">⏳</span>` : (ICON[x.state] || '⬜');
  const tg = x.target ? `<span class="tg">${esc(x.target)}</span>`
             : (x.detail ? `<span class="tg">${esc(x.detail)}</span>` : '');
  return `<li class="${x.state}"><span class="ic">${ic}</span>` +
         `<span class="nm">${esc(x.name)}</span>${tg}</li>`;
}

function render(d) {
  lastData = d;
  const secs = d.sections || {};
  // 合计所有板块的项，算总进度
  let allItems = [];
  SEC_ORDER.forEach(([key]) => {
    const s = secs[key];
    if (s && Array.isArray(s.items)) allItems = allItems.concat(s.items);
  });
  const n = allItems.length;
  const doneN = allItems.filter(x => ['ok','dup','unknown','fail','skip','done'].includes(x.state)).length;
  const pct = n ? Math.round(doneN / n * 100) : 0;

  document.getElementById('bar').style.width = pct + '%';
  document.getElementById('sub').textContent = d.finished
      ? `已完成　${fmt(d.updated_at)}`
      : `处理中　开始于 ${fmt(d.started_at)}　·　页面每秒自动刷新`;

  const okN = allItems.filter(x => x.state === 'ok').length;
  const failN = allItems.filter(x => x.state === 'fail').length;
  const runN = allItems.filter(x => x.state === 'running').length;
  document.getElementById('counts').innerHTML = [
    `全部 <b>${n}</b>`, `完成 <b>${okN}</b>`,
    runN ? `处理中 <b>${runN}</b>` : '',
    failN ? `失败 <b>${failN}</b>` : ''
  ].filter(Boolean).join('');

  // 「跳过卡死任务」按钮：有 running 或 fail 才显示
  const stuck = runN + failN;
  const tb = document.getElementById('toolbar');
  const btn = document.getElementById('btn-clear');
  tb.style.display = stuck ? 'flex' : 'none';
  btn.disabled = !!clearing;
  btn.textContent = clearing ? '清理中…' : `跳过卡死任务（${stuck}）`;

  document.getElementById('done').innerHTML = d.finished
      ? `<div class="done">✓ 处理完成　${d.note || ''}</div>` : '';

  // ★ 隐藏已处理完的板块（陈少 2026-10-16 定）：只有「还有 waiting/running」
  //   的板块才显示——处理完（全终态）的板块不占位置，免得误以为还有活要干。
  //   空板块（0 项）也一并隐藏。
  const visible = SEC_ORDER.map(([key, title, desc]) => {
    const s = secs[key];
    const items = (s && Array.isArray(s.items)) ? s.items : [];
    return { key, title, desc, items };
  }).filter(x => x.items.some(i => i.state === 'waiting' || i.state === 'running'));

  // 顶部总览卡：只显示有未完成任务的板块
  document.getElementById('overview').innerHTML = visible.map(x => {
    const done = x.items.filter(i => ['ok','dup','unknown','fail','skip','done'].includes(i.state)).length;
    const inner = `<span style="font-size:15px;font-weight:500;">${done}<span style="font-size:12px;color:var(--muted);">/${x.items.length}</span></span>`;
    return `<div class="ov"><div class="ov-t">${esc(x.title)}</div><div>${inner}</div></div>`;
  }).join('');

  // 详情区：只显示有未完成任务的板块
  const secHtml = visible.map(x => {
    const sub = x.desc ? `${x.desc} · ${x.items.length} 项` : `${x.items.length} 项`;
    return `<div class="sec"><div class="sec-h">${esc(x.title)}` +
           `<span class="sec-c">${esc(sub)}</span></div>` +
           `<ul>${x.items.map(itemHtml).join('')}</ul></div>`;
  }).join('');
  document.getElementById('sections').innerHTML = secHtml;

  // 全部处理完：给一句明确提示，别让页面显得空
  if (!visible.length) {
    document.getElementById('sections').innerHTML =
      `<div class="empty" style="padding:24px 4px;font-size:14px;">✓ 全部处理完成，没有待办任务。</div>`;
  }

  if (okN !== lastCount) {
    lastCount = okN;
    document.title = `项目处理进度（${doneN}/${n}）`;
  }
}

async function poll() {
  try {
    const r = await fetch('/progress.json?t=' + Date.now(), { cache: 'no-store' });
    if (r.ok) render(await r.json());
  } catch (e) { /* 服务还在起 / 暂时读不到，下一轮再试 */ }
}
poll();
setInterval(poll, 1000);
document.getElementById('btn-clear').addEventListener('click', clearStuck);
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

    def do_POST(self):              # noqa: N802
        path = self.path.split("?")[0]
        if path == "/clear":
            # 进度页「跳过卡死任务」按钮：清 running + fail 条目。
            import progress as _p
            try:
                result = _p.clear_stuck()
            except Exception as e:
                result = {"cleared": 0, "error": f"{type(e).__name__}: {e}"}
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        self._send(b"404", "text/plain; charset=utf-8", 404)

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
                "/open/log": ROOT / "_管理" / "_投递箱处理日志.md",
                "/open/report": ROOT / "_管理" / "_处理报告.md",
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
