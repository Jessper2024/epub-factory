#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
远川研究所 · 公众号原文批量下载 + EPUB 合订（2024-2026）

为什么本机跑：
    公众号历史文章列表(profile_ext)只在已登录微信的环境里才吐链接，
    远程沙箱没有登录态，所以「列表抓取」这一步必须在本机用你已登录
    微信网页版(wx.qq.com)的 Chrome 来完成。拿到列表后，单篇正文是公开
    可读的（无需登录），用标准库请求即可。

依赖（只需一个第三方库）：
    pip install playwright
    playwright install chromium

运行：
    python3 yuanchuan_crawler.py            # 一键：列表+正文+EPUB
    python3 yuanchuan_crawler.py --list      # 只抓列表 -> articles.json
    python3 yuanchuan_crawler.py --content   # 只抓正文 -> articles_full.json + images/
    python3 yuanchuan_crawler.py --epub      # 只合成 EPUB
    python3 yuanchuan_crawler.py --no-images # 不内嵌图片(EPUB 引用远程图，体积小)

断点续跑：每篇抓过的会跳过；中断后重跑同一命令即可接着来。
"""
import os, re, sys, json, time, html, argparse, datetime, urllib.request, urllib.error
from html.parser import HTMLParser

# ============================ 配置 ============================
BIZ = "MzIwMDY2NTgwMA=="          # 远川研究所公众号唯一 ID（已验证）
YEAR_START, YEAR_END = 2024, 2026  # 下载年份区间
HERE = os.path.dirname(os.path.abspath(__file__))
LIST_FILE = os.path.join(HERE, "articles.json")          # 列表中间产物
FULL_FILE = os.path.join(HERE, "articles_full.json")     # 正文中间产物
IMG_DIR = os.path.join(HERE, "images")
EPUB_PATH = os.path.join(HERE, "远川研究所_2024-2026.epub")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REF = "https://mp.weixin.qq.com/"

# Mac 上 Chrome 用户数据目录（带微信网页版 cookie 的那个 profile）
CHROME_PROFILE = os.path.expanduser("~/Library/Application Support/Google/Chrome")

DT_START = int(datetime.datetime(YEAR_START, 1, 1).timestamp())
DT_END = int(datetime.datetime(YEAR_END, 12, 31, 23, 59, 59).timestamp())


# ============================ 工具 ============================
def log(*a):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


def http_get(url, cookie="", binary=False, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        return data if binary else data.decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


# ---------- 单篇正文提取（沙箱已验证） ----------
class ContentEx(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_c = False
        self.depth = 0
        self.buf = []
    def handle_starttag(self, t, a):
        if t == "div" and dict(a).get("id") == "js_content":
            self.in_c = True
            self.depth = 1
            return
        if self.in_c:
            self.depth += 1
            self.buf.append(self.get_starttag_text())
    def handle_startendtag(self, t, a):
        if self.in_c:
            self.buf.append(self.get_starttag_text())
    def handle_endtag(self, t):
        if self.in_c:
            if t == "div":
                self.depth -= 1
                if self.depth <= 0:
                    self.in_c = False
                    return
            self.buf.append("</%s>" % t)
    def handle_data(self, d):
        if self.in_c:
            self.buf.append(d)


def extract_content(htm):
    p = ContentEx()
    p.feed(htm)
    return "".join(p.buf)


def clean_imgs(content, idx, download):
    """把正文里的图片 data-src 下载内嵌，或改成远程 https 引用"""
    out = content
    n = 0
    for m in re.finditer(r'<img\b[^>]*?>', content):
        tag = m.group(0)
        src = re.search(r'data-src="([^"]+)"', tag) or re.search(r'src="([^"]+)"', tag)
        if not src:
            continue
        u = src.group(1)
        if u.startswith("//"):
            u = "https:" + u
        if download:
            os.makedirs(IMG_DIR, exist_ok=True)
            fn = "img_%d_%d.jpg" % (idx, n)
            fp = os.path.join(IMG_DIR, fn)
            if not os.path.exists(fp):
                try:
                    data = http_get(u, binary=True, timeout=20)
                    if data:
                        open(fp, "wb").write(data)
                except Exception as e:
                    log("  图失败", u[:60], e)
                    n += 1
                    continue
            local = "../images/" + fn
            out = out.replace(src.group(0).split('="')[0] + '="%s"' % src.group(1), 'src="%s"' % local)
        else:
            out = out.replace(src.group(0).split('="')[0] + '="%s"' % src.group(1),
                              'src="%s"' % (u if u.startswith("http") else "https:" + u))
        n += 1
    return out


# ============================ 1. 列表抓取（需登录态） ============================
def grab_list():
    from playwright.sync_api import sync_playwright
    log("启动 Chrome（使用你的用户目录，需已登录微信网页版）...")
    arts = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False,
                                     args=["--user-data-dir=%s" % CHROME_PROFILE])
        page = browser.new_page()
        home = "https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=%s#wechat_redirect" % BIZ
        page.goto(home, wait_until="networkidle")
        time.sleep(3)
        src = page.content()
        tok = re.search(r'appmsg_token["\s:=]+["\']?([A-Za-z0-9_-]+)', src)
        token = tok.group(1) if tok else ""
        log("appmsg_token 取到:", token[:8] + "..." if token else "空(可能未登录)")
        cookies = {c["name"]: c["value"] for c in browser.contexts[0].cookies()}
        ck = "; ".join("%s=%s" % (k, v) for k, v in cookies.items())
        pt = cookies.get("pass_ticket", "")
        browser.close()

        offset = 0
        while True:
            api = ("https://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz=%s"
                   "&f=json&offset=%d&count=10&appmsg_token=%s&pass_ticket=%s"
                   % (BIZ, offset, token, pt))
            try:
                js = json.loads(http_get(api, ck))
            except Exception as e:
                log("列表请求失败(offset=%d):" % offset, e)
                break
            if js.get("ret") != 0:
                log("列表结束 ret=", js.get("ret"), js.get("errmsg"))
                break
            raw = js.get("msg_list") or "[]"
            msgs = json.loads(raw) if isinstance(raw, str) else raw
            if not msgs:
                log("列表为空，停止。")
                break
            got = 0
            for it in msgs:
                am = it.get("app_msg", {})
                ct = int(am.get("create_time", 0))
                if ct < DT_START:
                    log("已到 %d 年之前，停止翻页。" % YEAR_START)
                    offset = -1
                    break
                if DT_START <= ct <= DT_END:
                    arts.append({
                        "title": am.get("title", "").strip(),
                        "url": am.get("content_url", "").replace("&amp;", "&"),
                        "date": datetime.datetime.fromtimestamp(ct).strftime("%Y-%m-%d"),
                        "ts": ct,
                    })
                    got += 1
            log("offset=%d 本页%d篇，命中%d篇，累计%d篇" % (offset, len(msgs), got, len(arts)))
            if offset == -1:
                break
            if js.get("msg_count", 0) < 10:
                break
            offset += 10
            time.sleep(1.5)
    # 去重（按 url）
    seen, uniq = set(), []
    for a in arts:
        if a["url"] and a["url"] not in seen:
            seen.add(a["url"])
            uniq.append(a)
    uniq.sort(key=lambda x: x["ts"])
    json.dump(uniq, open(LIST_FILE, "w"), ensure_ascii=False, indent=1)
    log("列表完成，共 %d 篇 -> %s" % (len(uniq), LIST_FILE))
    return uniq


# ============================ 2. 正文抓取 ============================
def grab_content(download_img):
    if not os.path.exists(LIST_FILE):
        log("先跑列表：python3 yuanchuan_crawler.py --list")
        return
    arts = json.load(open(LIST_FILE))
    done = set()
    if os.path.exists(FULL_FILE):
        full = json.load(open(FULL_FILE))
        for a in full:
            done.add(a["url"])
    else:
        full = []
    for i, a in enumerate(arts):
        if a["url"] in done:
            continue
        try:
            htm = http_get(a["url"])
            if not htm:
                log("  正文空:", a["title"][:30])
                continue
            body = extract_content(htm)
            body = clean_imgs(body, i, download_img)
            a["content"] = body
            full.append(a)
            json.dump(full, open(FULL_FILE, "w"), ensure_ascii=False, indent=1)
            log("  [%d/%d] %s (%d字符)" % (i + 1, len(arts), a["title"][:30], len(body)))
        except Exception as e:
            log("  失败:", a["title"][:30], e)
        time.sleep(0.6)
    log("正文完成，共 %d 篇 -> %s" % (len(full), FULL_FILE))


# ============================ 3. 合成 EPUB ============================
def build_epub():
    import zipfile
    if not os.path.exists(FULL_FILE):
        log("先抓正文：python3 yuanchuan_crawler.py --content")
        return
    arts = json.load(open(FULL_FILE))
    arts.sort(key=lambda x: x.get("ts", 0))
    z = zipfile.ZipFile(EPUB_PATH, "w", zipfile.ZIP_DEFLATED)
    z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    z.writestr("META-INF/container.xml",
               '<?xml version="1.0"?>\n<container version="1.0" '
               'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
               '<rootfiles><rootfile full-path="OEBPS/content.opf" '
               'media-type="application/oebps-package+xml"/></rootfiles>\n</container>')

    man, spine, nav, chapters = [], [], [], []
    img_files = []
    if os.path.isdir(IMG_DIR):
        for fn in sorted(os.listdir(IMG_DIR)):
            if fn.lower().endswith((".jpg", ".png", ".gif", ".webp")):
                z.write(os.path.join(IMG_DIR, fn), "OEBPS/images/" + fn)
                man.append('<item id="%s" href="images/%s" media-type="image/jpeg"/>' % (fn, fn))
                img_files.append(fn)

    for i, a in enumerate(arts):
        cid = "a%04d" % i
        title = html.escape(a.get("title", "无标题"))
        body = a.get("content", "")
        # 图片本地引用修正（content 里 ../images/xxx）
        xhtml = ("<?xml version='1.0' encoding='utf-8'?>\n"
                 "<!DOCTYPE html><html xmlns='http://www.w3.org/1999/xhtml' "
                 "xmlns:epub='http://www.idpf.org/2007/ops'>\n<head>"
                 "<meta charset='utf-8'/><title>%s</title></head>\n<body>"
                 "<h1>%s</h1><p><small>%s · 远川研究所</small></p><hr/>%s</body></html>"
                 % (title, title, html.escape(a.get("date", "")), body))
        z.writestr("OEBPS/%s.xhtml" % cid, xhtml)
        man.append('<item id="%s" href="%s.xhtml" media-type="application/xhtml+xml"/>' % (cid, cid))
        spine.append('<itemref idref="%s"/>' % cid)
        nav.append('<navPoint id="n%s" playOrder="%d"><navLabel><text>%s</text></navLabel>'
                   '<content src="%s.xhtml"/></navPoint>' % (cid, i + 1, title, cid))

    opf = ("<?xml version='1.0' encoding='utf-8'?>\n"
           "<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='bk'>\n"
           "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>\n"
           "<dc:identifier id='bk'>yuanchuan-2024-2026</dc:identifier>\n"
           "<dc:title>远川研究所 文章合订 2024-2026</dc:title>\n"
           "<dc:creator>远川研究所</dc:creator>\n"
           "<dc:language>zh-CN</dc:language>\n"
           "<meta property='dcterms:modified'>%s</meta>\n"
           "</metadata>\n<manifest>\n"
           "<item id='ncx' href='toc.ncx' media-type='application/x-dtbncx+xml'/>\n"
           "%s\n</manifest>\n<spine toc='ncx'>\n%s\n</spine>\n</package>\n"
           % (datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "\n".join(man), "\n".join(spine)))
    z.writestr("OEBPS/content.opf", opf)

    ncx = ("<?xml version='1.0' encoding='utf-8'?>\n"
           "<ncx xmlns='http://www.daisy.org/z3986/2005/ncx/' version='2005-1'>\n"
           "<head><meta name='dtb:uid' content='yuanchuan-2024-2026'/></head>\n"
           "<docTitle><text>远川研究所 2024-2026</text></docTitle>\n"
           "<navMap>\n%s\n</navMap>\n</ncx>\n" % "\n".join(nav))
    z.writestr("OEBPS/toc.ncx", ncx)
    z.close()
    log("EPUB 完成：%s （%d 篇）" % (EPUB_PATH, len(arts)))


# ============================ 入口 ============================
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只抓列表")
    ap.add_argument("--content", action="store_true", help="只抓正文")
    ap.add_argument("--epub", action="store_true", help="只合成EPUB")
    ap.add_argument("--no-images", action="store_true", help="不内嵌图片")
    args = ap.parse_args()
    do_img = not args.no_images

    if args.list:
        grab_list()
    elif args.content:
        grab_content(do_img)
    elif args.epub:
        build_epub()
    else:
        grab_list()
        grab_content(do_img)
        build_epub()
    log("全部完成。")
