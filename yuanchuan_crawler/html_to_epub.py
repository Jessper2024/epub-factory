#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
远川研究所 · 微信原文 HTML -> EPUB 合订（2024-2026）

分工：
    你（陈少）本机把公众号文章的「微信原文 HTML」导出到 raw_html/ 目录，
    本脚本负责扫描这些 HTML，按 标题/日期/正文 提取，合成一本 EPUB。

输入规范（详见 README.md）：
    raw_html/ 下任意层级的 *.html / *.htm 均可（推荐 Chrome「网页，完整」另存，
    会得到  aaa.html + aaa_files/ 图片夹，本脚本自动关联）。
    单篇 .html（图片走远程 data-src）也支持，构建时会自动下载图片进 EPUB。

运行：
    python3 html_to_epub.py                 # 扫描 raw_html/ -> 远川研究所_2024-2026.epub
    python3 html_to_epub.py --no-images     # 不内嵌图片，体积更小（EPUB 引用远程图）
    python3 html_to_epub.py --out my.epub   # 指定产物名
    python3 html_to_epub.py --raw ./other  # 指定别的 HTML 目录

零第三方依赖（只用 Python 标准库）。
"""
import os, re, sys, csv, json, html, argparse, datetime, urllib.request, urllib.error, zipfile, base64
from html.parser import HTMLParser

# ============================ 配置 ============================
HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw_html")
EPUB_PATH = os.path.join(HERE, "微信文章合订.epub")  # 默认产物名，可用 --out 覆盖
LOG_PATH = os.path.join(HERE, "raw_html", "processing_log.csv")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REF = "https://mp.weixin.qq.com/"

# 标题/日期提取失败时的兜底
YEAR_START, YEAR_END = 2024, 2026


# ============================ 工具 ============================
def log(*a):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


def http_get(url, binary=False, timeout=25):
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        return data if binary else data.decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


# ---------- 正文提取：匹配 <div id="js_content"> ... </div> ----------
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


def meta(htm, prop):
    """取 <meta property="og:xxx" content="..."> 或 name= 的内容"""
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(prop), htm, re.I)
    if m:
        return m.group(1).strip()
    # 顺序反过来：content 在前
    m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(prop), htm, re.I)
    return m.group(1).strip() if m else ""


def get_title(htm):
    t = meta(htm, "og:title")
    if t:
        return t
    m = re.search(r'<h1[^>]*id=["\']activity-name["\'][^>]*>(.*?)</h1>', htm, re.S | re.I)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    m = re.search(r"<title>(.*?)</title>", htm, re.S | re.I)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return "无标题"


def get_date(htm):
    """优先 var ct=Unix时间戳（最准），其次 <span id="publish_time">"""
    m = re.search(r'var\s+ct\s*=\s*["\']?(\d{10})["\']?', htm)
    if m:
        ts = int(m.group(1))
        if 1388500000 < ts < 2000000000:  # 2014~2033 合理区间
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), ts
    m = re.search(r'<span[^>]*id=["\']publish_time["\'][^>]*>(.*?)</span>', htm, re.S | re.I)
    if m:
        s = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if re.match(r"\d{4}[-/年]\d{1,2}", s):
            # 归一化 2024年01月15日 / 2024-01-15
            s2 = s.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
            parts = [int(x) for x in re.findall(r"\d+", s2)[:3]]
            try:
                d = datetime.date(*parts)
                return d.strftime("%Y-%m-%d"), int(datetime.datetime(d.year, d.month, d.day).timestamp())
            except Exception:
                pass
    return "", 0


def get_sn(htm):
    """从 og:url 取 sn 作为去重键"""
    u = meta(htm, "og:url")
    if u:
        m = re.search(r"[?&]sn=([A-Za-z0-9_-]+)", u)
        if m:
            return m.group(1)
    return ""


# ---------- 图片处理：本地优先，远程下载 ----------
import urllib.parse, shutil

IMG_EXT_MT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


def _new_img_name(used, ext):
    fn = "img_%05d%s" % (used[0], ext)
    used[0] += 1
    return fn


def collect_images(content, html_path, epub_img_dir, used, download=True):
    """
    扫 content 内 <img>，按以下优先级解析真实图源并落盘到 epub_img_dir：
        1) data-src（微信真图，多为 CDN 远程）-> 下载
        2) src（可能是本地 _files 夹文件，或远程）-> 远程下载 / 本地复制
    远程下载失败时自动回退到同标签里的本地 src（Chrome「网页，完整」另存即有）。
    返回改写后的 content（图片引用统一改为 images/xxx.ext）。
    """
    base_dir = os.path.dirname(html_path)

    def normalize(u):
        if not u:
            return None
        u = u.strip()
        if u.startswith("data:image"):
            return None  # base64 占位 1x1，跳过
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http"):
            return u  # 远程 URL
        cand = os.path.normpath(os.path.join(base_dir, u))
        if os.path.exists(cand):
            return ("local", cand)
        return None

    def save_local(src_path):
        ext = os.path.splitext(src_path)[1].lower() or ".jpg"
        fn = _new_img_name(used, ext)
        shutil.copyfile(src_path, os.path.join(epub_img_dir, fn))
        return fn

    def save_remote(url):
        try:
            data = http_get(url, binary=True, timeout=20)
        except Exception as e:
            log("  图下载失败:", url[:60], e)
            return None
        if not data:
            return None
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in IMG_EXT_MT:
            ext = ".jpg"
        fn = _new_img_name(used, ext)
        open(os.path.join(epub_img_dir, fn), "wb").write(data)
        return fn

    def save_inline(data_uri):
        """data:image/xxx;base64,AAAA -> 解码写成图片文件，返回 EPUB 内文件名"""
        try:
            header, _, b64 = data_uri.partition(",")
            mm = re.search(r"data:image/([\w+.-]+)", header)
            ext = mm.group(1).lower() if mm else "jpg"
            ext = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif",
                   "webp": "webp", "bmp": "bmp"}.get(ext, "jpg")
            raw = base64.b64decode(b64)
            if len(raw) < 32:
                return None
            fn = _new_img_name(used, "." + ext)
            open(os.path.join(epub_img_dir, fn), "wb").write(raw)
            return fn
        except Exception as e:
            log("  内联图解码失败:", str(e)[:50])
            return None

    def rewrite(tag, fn):
        new_tag = re.sub(r'data-src="[^"]*"', "", tag, count=1)
        new_tag = re.sub(r'src="[^"]*"', 'src="images/%s"' % fn, new_tag, count=1)
        if "src=" not in new_tag:
            new_tag = re.sub(r'/?>$', ' src="images/%s">' % fn, new_tag, count=1)
        return new_tag

    def cb(m):
        tag = m.group(0)
        ds = re.search(r'data-src="([^"]+)"', tag)
        s = re.search(r'(?<!data-)src="([^"]+)"', tag)  # 排除 data-src 里的 src
        cands = [c.group(1) for c in (ds, s) if c]  # 优先 data-src，其次 src
        for u in cands:
            if u.startswith("data:image"):  # OpenClaw/SingleFile 等内联 base64 图
                fn = save_inline(u)
                if fn:
                    return rewrite(tag, fn)
                continue
            r = normalize(u)
            if r is None:
                continue
            if isinstance(r, tuple) and r[0] == "local":
                fn = save_local(r[1])
                return rewrite(tag, fn)
            else:
                fn = save_remote(r)
                if fn:
                    return rewrite(tag, fn)
                # 远程失败 -> 继续尝试下一个候选（如本地 src）
                continue
        log("  图无法获取（data-src 远程失败且无本地 src）:", tag[:80])
        return tag

    new_content = re.sub(r'<img\b[^>]*?>', cb, content)
    return new_content


# ---------- 把宽松 HTML 片段洗成良构 XHTML（EPUB 章节必须是合法 XML）----------
def clean_xhtml(body):
    try:
        from lxml import html as lh
        from lxml import etree
        frag = lh.fragment_fromstring(body, create_parent="div")
        return etree.tostring(frag, encoding="unicode", method="xml")
    except Exception as e:
        # 无 lxml 或解析失败：退化为最小清理（自闭常见 void 标签、转义裸 &）
        log("  正文 XML 清洗退回基础模式:", str(e)[:60])
        return body


VOID_TAGS = ("img", "br", "hr", "meta", "link", "source", "col", "area",
             "base", "embed", "param", "track", "wbr", "input")


def selfclose_void(body):
    """把未自闭的 void 元素统一改成 <x .../>（XML 必需）"""
    pat = r'<(%s)\b([^>]*?)(?<!/)>' % "|".join(VOID_TAGS)
    return re.sub(pat, lambda m: "<%s%s/>" % (m.group(1), m.group(2).rstrip()), body)


# ============================ 扫描 + 合成 ============================
def build(download_img=True, out_path=EPUB_PATH, raw_dir=RAW_DIR):
    htmls = []
    for root, _, files in os.walk(raw_dir):
        for fn in files:
            if fn.lower().endswith((".html", ".htm")):
                htmls.append(os.path.join(root, fn))
    if not htmls:
        log("raw_html/ 下没有找到任何 HTML 文件。把微信原文 HTML 丢进去再跑。")
        return

    log("扫描到 %d 个 HTML 文件，开始提取..." % len(htmls))
    arts = []
    rows = []
    seen = set()
    epub_img_dir = os.path.join(HERE, "epub_images")
    if download_img:
        os.makedirs(epub_img_dir, exist_ok=True)
    used = [0]  # 全局图片计数，避免跨篇重名
    for i, hp in enumerate(htmls):
        try:
            with open(hp, "r", encoding="utf-8", errors="ignore") as f:
                htm = f.read()
        except Exception as e:
            rows.append([os.path.basename(hp), "读取失败", "", "", "", str(e)])
            continue
        title = get_title(htm)
        date, ts = get_date(htm)
        sn = get_sn(htm)
        key = sn or (title + "|" + date)
        if key in seen:
            rows.append([os.path.basename(hp), "重复跳过", title, date, "", sn])
            continue
        seen.add(key)
        body = extract_content(htm)
        # 洗成良构 XHTML（自闭 void 标签、转义 &、收拢未闭合自定义标签）
        try:
            body = clean_xhtml(body)
        except Exception:
            pass
        if not body.strip():
            rows.append([os.path.basename(hp), "正文空", title, date, "", sn])
            continue
        # 图片：本地 _files 夹复制 + 远程 data-src 下载
        if download_img:
            body = collect_images(body, hp, epub_img_dir, used)
        body = selfclose_void(body)  # void 标签统一自闭，保证 XHTML 良构
        nimg = body.count('src="images/')
        arts.append({"title": title, "date": date, "ts": ts or (1e12 - i), "content": body})
        rows.append([os.path.basename(hp), "OK", title, date, str(nimg), sn])
        log("  [%d/%d] %s 图%d" % (i + 1, len(htmls), title[:30], nimg))

    if not arts:
        log("没有可合成的合格文章。")
        return

    arts.sort(key=lambda x: (x.get("ts") or 0))
    log("提取完成 %d 篇，开始合成 EPUB..." % len(arts))

    # 写处理日志
    with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["文件", "状态", "标题", "日期", "图片数", "sn"])
        w.writerows(rows)

    # 图片清单统一从 epub_images/ 目录扫描（远程下载 + 本地复制都已落盘）
    z = zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED)
    z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    z.writestr("META-INF/container.xml",
               '<?xml version="1.0"?>\n<container version="1.0" '
               'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
               '<rootfiles><rootfile full-path="OEBPS/content.opf" '
               'media-type="application/oebps-package+xml"/></rootfiles>\n</container>')

    man, spine, nav = [], [], []
    # 图片清单：扫描 epub_images 目录
    img_files = []
    if os.path.isdir(epub_img_dir):
        for fn in sorted(os.listdir(epub_img_dir)):
            if fn.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
                z.write(os.path.join(epub_img_dir, fn), "OEBPS/images/" + fn)
                mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}.get(fn.lower().split(".")[-1], "image/jpeg")
                man.append('<item id="%s" href="images/%s" media-type="%s"/>' % (fn, fn, mt))
                img_files.append(fn)

    for i, a in enumerate(arts):
        cid = "a%04d" % i
        title = html.escape(a.get("title", "无标题"))
        body = a.get("content", "")
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
           % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "\n".join(man), "\n".join(spine)))
    z.writestr("OEBPS/content.opf", opf)

    ncx = ("<?xml version='1.0' encoding='utf-8'?>\n"
           "<ncx xmlns='http://www.daisy.org/z3986/2005/ncx/' version='2005-1'>\n"
           "<head><meta name='dtb:uid' content='yuanchuan-2024-2026'/></head>\n"
           "<docTitle><text>远川研究所 2024-2026</text></docTitle>\n"
           "<navMap>\n%s\n</navMap>\n</ncx>\n" % "\n".join(nav))
    z.writestr("OEBPS/toc.ncx", ncx)
    z.close()
    log("EPUB 完成：%s （%d 篇，%d 张图）" % (out_path, len(arts), len(img_files)))
    log("处理日志：%s" % LOG_PATH)


# ============================ 入口 ============================
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true", help="不内嵌图片")
    ap.add_argument("--out", default=EPUB_PATH, help="EPUB 输出路径")
    ap.add_argument("--raw", default=RAW_DIR, help="HTML 源目录")
    args = ap.parse_args()
    build(download_img=not args.no_images, out_path=args.out, raw_dir=args.raw)
    log("全部完成。")
