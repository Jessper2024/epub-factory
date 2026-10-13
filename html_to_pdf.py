#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notion 网页快照 HTML → 打印用 PDF（陈少 2026-10-13 定，PDF 独立线）。

与 EPUB 线**互不干涉**（陈少明确）：EPUB 是给阅读器的，PDF 是给打印/存档的。

版式参考陈少给的样例（`4.44 万亿社保就业支出背后… _ Notion.pdf`）：
    [页面图标]
    标题（大号）
    ──────────────
    属性表（文章链接 / 作者 / 发布时间 / 来源 …，**全部展开**）
    ──────────────
    正文（含图片）
    ──────────────
    第 N/M 页

硬约束（陈少定）
    1. **字体 = 方正屏显臻宋**（本机 `~/Library/Fonts/方正屏显臻宋.TTF`）。
       必须 base64 内嵌进 CSS —— Chrome 打印不嵌字体，PDF 里字体会丢。
    2. **A4 纸张**（595×842pt）。Chrome 无头默认是 Letter，必须显式指定。
    3. **图片原样嵌入、绝不切割**：`break-inside: avoid`，放不下就整张推到下一页。
       这一条是整个 PDF 线最容易出错的地方，出锅后必须实测。

输入形态：**只支持 Notion 网页快照**（`body.notion-body` + `#notion-app`）。
Notion 的静态 HTML 导出（`article.page`）结构完全不同，不处理（陈少 2026-10-13 明确）。
"""
from __future__ import annotations

import argparse
import base64
import json
import html as html_mod
import os
import time
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from lxml import html as LH

ROOT = Path(__file__).resolve().parent.parent

# 屏显臻宋（陈少 2026-10-13 定）
FONT_PATH = Path.home() / "Library" / "Fonts" / "方正屏显臻宋.TTF"
FONT_FAMILY = "PingXianZhenSong"

# 平台图标（白底彩框，_engine/assets/platform-icons/，陈少 2026-10-13 定）
ICON_DIR = Path(__file__).resolve().parent / "assets" / "platform-icons"
# 来源/类型 → 图标 key
PLATFORM_KEY = {
    "微信公众号": "wechat", "微信": "wechat",
    "知乎": "zhihu",
    "Twitter": "twitter", "推特": "twitter", "X": "twitter",
    "微博": "weibo",
    "小红书": "xiaohongshu",
}
# 链接域名 → 平台名（兜底：属性表抽不到「来源/类型」时靠它，图标/类型行永远有）
DOMAIN_PLATFORM = (
    ("mp.weixin.qq.com", "微信公众号"),
    ("zhihu.com", "知乎"),
    ("x.com", "Twitter"),
    ("twitter.com", "Twitter"),
    ("weibo.com", "微博"),
    ("weibo.cn", "微博"),
    ("xiaohongshu.com", "小红书"),
    ("xhslink.com", "小红书"),
)

# 「其他 N 个属性」的 N —— Notion 属性表标配的折叠提示（照截图）
OTHER_COUNT = 9

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def log(*a):
    print(" ", *a, flush=True)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# ---------------------------------------------------------------- 抽取

def extract(doc) -> dict:
    """从 Notion 网页快照里抽：图标 / 标题 / 属性表 / 正文块（段落+图片，保序）。

    属性表在快照里是随机 hash class（x87ps6o…），抓不稳；
    所以按**标签文本**兜底：找「作者」「发布时间」这类 label 所在的行。
    """
    out = {"icon": "", "title": "", "props": [], "blocks": []}

    # 页面图标
    ic = doc.xpath('//*[contains(@class,"notion-page-icon")]//img')
    if ic:
        out["icon"] = ic[0].get("src") or ""

    # 标题：优先 <title>（去掉 " | Notion" 后缀）
    tl = doc.xpath("//title/text()")
    if tl:
        out["title"] = clean(tl[0]).replace(" | Notion", "").replace(" - Notion", "")

    # 属性表：Notion 快照里是随机 hash class，抓不稳；
    # 按 label 文本逐层往上找，直到容器里出现「label 之外的文本」——那层就是
    # 「标签+值」的粘连行，把值切出来（陈少 2026-10-13 实测定的）。
    labels = ("URL", "文章链接", "链接", "作者", "发布时间", "来源", "类型", "平台")
    # Notion 的 UI 提示语/占位文字，不是真属性值（实测抽到过「空白正在思考...」）
    noise = ("空白", "正在思考", "添加封面", "添加验证", "自定义布局", "添加评论",
             "添加描述", "添加图标", "添加", "输入", "点击此处")
    for lab in labels:
        hits = doc.xpath('//*[text()="%s"]' % lab)
        if not hits:
            continue
        n = hits[0]
        for _ in range(8):                           # 往上最多 8 层
            n = n.getparent()
            if n is None:
                break
            txt = clean("".join(n.itertext()))
            if txt and txt != lab and txt.startswith(lab):
                val = txt[len(lab):].strip()
                if val and len(val) < 300 and not any(x in val for x in noise):
                    out["props"].append((lab, val))
                break

    # 正文块：**只用 notion-page-content**（陈少 2026-10-13 实测定的）。
    # 用整个 doc / notion-frame 会把 Notion 左侧 sidebar 的「最近访问」图标
    # 当成正文图（实测：30 张真图其实全是 sidebar 的 record-icon）——踩过。
    body = None
    for cand in doc.xpath('//*[contains(@class,"notion-page-content")]'):
        body = cand
        break
    if body is None:
        return out

    seen_prev = None
    for el in body.iter():
        cls = el.get("class") or ""
        if "notion-text-block" in cls or "notion-paragraph" in cls:
            tx = clean("".join(el.itertext()))
            if tx and tx != out["title"] and tx != seen_prev:
                out["blocks"].append(("p", tx))
                seen_prev = tx
        elif el.tag == "img":
            src = el.get("src") or ""
            # 真图判定：data URI 且够大（小的是 Notion 图标/装饰，正文图不会太小）
            if src.startswith("data:image/") and len(src) > 3000:
                out["blocks"].append(("img", src))
    return out


# ---------------------------------------------------------------- 组装

def _font_face() -> str:
    if not FONT_PATH.exists():
        log("⚠ 字体不存在：%s（退回系统宋体）" % FONT_PATH)
        return ""
    data = base64.b64encode(FONT_PATH.read_bytes()).decode("ascii")
    log("字体已内嵌：%s（%.1fMB）" % (FONT_PATH.name, FONT_PATH.stat().st_size / 1048576))
    return ("@font-face{font-family:'%s';src:url(data:font/ttf;base64,%s) format('truetype');}"
            % (FONT_FAMILY, data))


# 属性行图标（照陈少截图：链接/圆形/日历）
PROP_ICON = {
    "URL": "\U0001f517", "文章链接": "\U0001f517", "链接": "\U0001f517",
    "作者": "\u25ce", "来源": "\u25ce", "类型": "\u25ce", "平台": "\u25ce",
    "发布时间": "\U0001f4c5", "日期": "\U0001f4c5",
}
# 有胶囊底色的属性（照陈少截图：作者=橙、来源=绿；其余纯文本）
PROP_PILL = {
    "作者": "pill-amber",
    "来源": "pill-green",
    "类型": "pill-purple",
    "平台": "pill-purple",
}


def platform_from_url(url: str) -> str:
    """链接域名 → 平台名（认不出返回空）。"""
    u = (url or "").strip().lower()
    for domain, name in DOMAIN_PLATFORM:
        if domain in u:
            return name
    return ""


def platform_icon_data_uri(platform_name: str) -> str:
    """平台名 → 图标的 base64 data URI（找不到返回空）。"""
    key = PLATFORM_KEY.get((platform_name or "").strip())
    if not key:
        return ""
    path = ICON_DIR / ("%s.png" % key)
    if not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return "data:image/png;base64," + b64


def build_html(info: dict, platform: str = "") -> str:
    """拼打印用 HTML。**版式严格对齐陈少的截图**（2026-10-13 定）。

    标题块：**平台图标在左、标题在右**（图标贴标题块左侧垂直居中）。
    「其他 N 个属性」**不写**（陈少 2026-10-13：那是网页打印扫描出来的，不算数）。
    """
    parts = []
    icon_uri = platform_icon_data_uri(platform)
    if icon_uri:
        parts.append('<div class="ptitle-row"><img class="pico-img" src="%s">'
                     '<h1 class="ptitle">%s</h1></div>'
                     % (icon_uri, html_mod.escape(info["title"])))
    else:
        parts.append('<h1 class="ptitle">%s</h1>' % html_mod.escape(info["title"]))
    _ = info.get("icon")            # Notion 页面图标不再用（平台图标取代）

    if info["props"]:
        rows = []
        for k, v in info["props"]:
            icon = PROP_ICON.get(k, "\u25ce")
            cls = PROP_PILL.get(k, "")
            val = ('<span class="pill %s">%s</span>' % (cls, html_mod.escape(v))
                   if cls else html_mod.escape(v))
            rows.append(
                '<div class="prow"><span class="pico">%s</span>'
                '<span class="pkey">%s</span><span class="pval">%s</span></div>'
                % (icon, html_mod.escape(k), val)
            )
        parts.append('<div class="ptable">%s</div>' % "".join(rows))

    for kind, val in info["blocks"]:
        if kind == "p":
            parts.append('<p>%s</p>' % html_mod.escape(val))
        else:
            parts.append('<figure class="pic"><img src="%s"></figure>' % val)

    css = """
    @page { size: A4; margin: 18mm 16mm; }
    html, body { margin:0; padding:0; background:#ffffff !important;
                 color:#1a1a1a !important; }
    body { font-family:'PingXianZhenSong','Songti SC',serif;
           color:#1a1a1a !important; background:#ffffff !important;
           font-size:11.5pt; line-height:1.85; }
    .ptitle-row { display:flex; align-items:center; gap:20px; margin:22px 0 26px; }
    .pico-img { width:64px; height:64px; flex:none; }
    .ptitle { font-size:26pt; font-weight:600; line-height:1.5;
              margin:0; letter-spacing:.5px; }
    .ptable { margin:0 0 26px; font-size:10.5pt; }
    .prow { display:flex; align-items:center; line-height:1.9; }
    .pico { width:1.6em; flex:none; color:#9b9691; font-size:11pt; }
    .pkey { width:6.5em; flex:none; color:#8a8580; }
    .pval { color:#37352f; word-break:break-all; }
    .prow.more .pkey { color:#a5a09b; }
    .pill { padding:1px 8px; border-radius:3px; font-size:10pt; }
    .pill-amber { background:#fadec9; color:#5c3b12; }
    .pill-green { background:#dbeddb; color:#1c3829; }
    .pill-purple { background:#e8deee; color:#412454; }
    p { margin:0 0 0.9em; text-align:justify; }
    figure.pic { margin:1.1em 0; text-align:center;
                 break-inside:avoid; page-break-inside:avoid; }
    figure.pic img { max-width:100%; max-height:240mm; height:auto; }
    """
    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<style>" + _font_face() + css + "</style></head><body>"
            + "".join(parts) + "</body></html>")


# ---------------------------------------------------------------- 打印

FOOTER_TMPL = (
    '<div style="width:100%;font-size:9pt;color:#8a8580;'
    'font-family:\'PingXianZhenSong\',serif;text-align:center;">'
    '第 <span class="pageNumber"></span>/<span class="totalPages"></span> 页'
    '</div>'
)


def to_pdf(src_html: Path, out_pdf: Path) -> bool:
    """HTML → A4 PDF，页脚「第 N/M 页」。

    必须走 **Chrome DevTools Protocol**：命令行的 `--print-to-pdf` 不支持自定义
    页脚（`--no-pdf-header-footer` 就没有页码，默认页脚又是 `1/1` 格式）。
    只有 CDP 的 `Page.printToPDF` 能传 `footerTemplate` / `displayHeaderFooter`。
    """
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    port = 9333 + (os.getpid() % 500)          # 避开冲突
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage", "--remote-debugging-port=%d" % port,
         "--remote-allow-origins=*",
         "--user-data-dir=/tmp/chrome-pdf-%d" % port,
         src_html.as_uri()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait_ws(port):
            log("✗ 连不上 Chrome 调试端口")
            return False
        pdf_b64 = _cdp_print("", port)
        if not pdf_b64:
            return False
        out_pdf.write_bytes(base64.b64decode(pdf_b64))
        return out_pdf.stat().st_size > 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _wait_ws(port: int, tries: int = 50):
    """等 Chrome 的调试端口起来。"""
    import urllib.request
    for _ in range(tries):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port,
                                       timeout=1) as r:
                return r.read()
        except Exception:
            time.sleep(0.2)
    return None


def _cdp_print(ws_url: str, port: int) -> str:
    """用 CDP 的 Page.printToPDF 出 PDF（带「第 N/M 页」页脚）。"""
    import json
    import urllib.request
    try:
        r = urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=5)
        targets = json.loads(r.read().decode())
        page = next((x for x in targets if x.get("type") == "page"), None)
        if not page:
            return ""
        # 直接 HTTP 调用 CDP 的 /json/... 不行，需要 websocket；
        # 这里用极简 websocket 客户端（只发一条命令、读一条回执）。
        # 先让页面加载完再打印（否则空白页——踩过）。
        # about:blank 时 Chrome 起得快、内容还没解析；用 Page.navigate 显式导航
        # 并轮询 document.readyState，直到 complete。
        url = page.get("url") or ""
        if url and not url.startswith("data:"):
            _ws_call(page["webSocketDebuggerUrl"], {
                "id": 100, "method": "Page.navigate", "params": {"url": url}})
        for _ in range(40):
            st = _ws_eval(page["webSocketDebuggerUrl"],
                          "document.readyState + '|' + (document.body ? document.body.innerText.length : 0)")
            if st and st.startswith("complete") and st.split("|")[-1] != "0":
                break
            time.sleep(0.25)
        return _ws_call(page["webSocketDebuggerUrl"], {
            "id": 200, "method": "Page.printToPDF",
            "params": {
                "printBackground": True,
                "paperWidth": 8.27, "paperHeight": 11.69,      # A4 inches
                "preferCSSPageSize": True,     # 边距交给 CSS @page（英寸边距会和 CSS 打架，右侧溢出——踩过）
                "displayHeaderFooter": True,
                "headerTemplate": "<div></div>",
                "footerTemplate": FOOTER_TMPL,
            },
        })
    except Exception as e:  # noqa: BLE001
        log("✗ CDP 调用失败：", str(e)[:120])
        return ""


def _ws_eval(ws_url: str, expr: str) -> str:
    """在页面里跑 JS，返回字符串结果（等加载完成用）。"""
    result = _ws_call(ws_url, {"id": 300, "method": "Runtime.evaluate",
                               "params": {"expression": expr, "returnByValue": True}})
    return result or ""


def _ws_call(ws_url: str, message: dict) -> str:
    """用 websocket-client 库发一条 CDP 命令，返回 result 里的 data/value 字符串。"""
    import websocket
    try:
        ws = websocket.create_connection(ws_url, timeout=60)
        ws.send(json.dumps(message))
        data = b""
        want = message.get("method", "")
        while True:
            chunk = ws.recv()
            if isinstance(chunk, str):
                chunk = chunk.encode()
            data += chunk
            if b'"result"' in data or b'"error"' in data:
                break
            if len(data) > 100_000_000:      # 防爆（100MB PDF 足够）
                break
        ws.close()
    except Exception as e:  # noqa: BLE001
        log("✗ WebSocket：", str(e)[:120])
        return ""
    import re as _re
    m = _re.search(rb'"data"\s*:\s*"([A-Za-z0-9+/=]+)"', data)
    if m:
        return m.group(1).decode()
    mv = _re.search(rb'"value"\s*:\s*"([^"]*)"', data)
    return mv.group(1).decode() if mv else ""


def convert_one(src: Path, out_pdf: Path) -> bool:
    raw = src.read_bytes()
    doc = LH.fromstring(raw, parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
    if "notion-body" not in (doc.get("class") or "") and not doc.xpath('//*[@id="notion-app"]'):
        log("⚠ 不是 Notion 网页快照，已在处理范围之外：", src.name[:50])
    info = extract(doc)
    # 平台名三级兜底：来源 → 类型 → 链接域名（陈少 2026-10-13：图标/类型行永远要有）
    platform = ""
    for k, v in info["props"]:
        if k in ("来源", "类型", "平台") and v and v not in ("空白", "正在思考"):
            platform = v
            break
    if not platform:
        # URL 从属性表第一项（URL/文章链接/链接）取
        url = next((v for k, v in info["props"] if k in ("URL", "文章链接", "链接")), "")
        platform = platform_from_url(url)
    # 属性表补「类型」行：抽不到就用平台判定结果（保持 4 行齐）
    if platform and not any(k == "类型" for k, _ in info["props"]):
        info["props"].insert(1, ("类型", platform))
    log("标题：%s" % (info["title"][:40] or "（无）"))
    log("属性 %d 行 · 正文块 %d 个（图 %d 张）"
        % (len(info["props"]), len(info["blocks"]),
           sum(1 for k, _ in info["blocks"] if k == "img")))
    html = build_html(info, platform)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(html)
        tmp_html = Path(fh.name)
    try:
        return to_pdf(tmp_html, out_pdf)
    finally:
        tmp_html.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Notion 网页快照 HTML → 打印 PDF")
    ap.add_argument("inputs", nargs="+", type=Path, help="输入的 .html")
    ap.add_argument("-o", "--outdir", type=Path, default=None,
                    help="输出目录（默认与输入同目录）")
    args = ap.parse_args()

    ok = 0
    for src in args.inputs:
        if not src.exists():
            log("✗ 不存在：", src)
            continue
        outdir = args.outdir or src.parent
        out = outdir / (src.stem.replace(" _ Notion", "") + ".pdf")
        log("→ %s" % src.name[:50])
        if convert_one(src, out):
            log("✓ %s（%.2fMB）" % (out.name, out.stat().st_size / 1048576))
            ok += 1
    log("完成 %d / %d" % (ok, len(args.inputs)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
