#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sigil XHTML → 合订 EPUB（按公众号分本，按月分章）

本项目位置（2026-09-16 定案，长期不变）
    代码  ~/Life/EPUB制作/_engine/         本文件所在目录
    数据  ~/Life/EPUB制作/<公众号名>/       xhtml/ · 原始HTML/ · cover.jpg · *.epub
    收件箱 ~/Life/EPUB制作/_待处理/

目录约定
    ~/Life/EPUB制作/<公众号名>/         该号所有 -Sigil.xhtml

结构约定
    月份  → h1      （如「2026年8月」）
    文章标题 → h2   （每篇一个 h2，文内小标题降级为 h3）
    排序  → 从旧到新

用法
    python3 build_epub.py                      # 重建全部公众号
    python3 build_epub.py --only 猫笔刀         # 只重建某一本
    python3 build_epub.py --add a.xhtml b.html # 归档新文件并自动重建对应 EPUB
    python3 build_epub.py --list               # 只看扫描结果，不打包

--add 会自动识别文件属于哪个公众号（文件名 → meta 行 → HTML 的 #js_name），
归档到对应目录；遇到 .html 会先调 sigi_convert.py 转成 XHTML。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promo as PROMO  # noqa: E402

XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
NSMAP = {None: XHTML_NS, "epub": EPUB_NS}

ENGINE_DIR = Path(__file__).resolve().parent          # _engine/（代码所在目录）
ROOT = ENGINE_DIR.parent                              # ~/Life/EPUB制作（数据根目录）
SIGI_DIR = ENGINE_DIR                                 # sigi_convert.py 与本项目同目录

# 每个公众号一个独立文件夹，互不干扰：
#   <号名>/xhtml/*.xhtml   原始文档备份（不可删，EPUB 由它重建）
#   <号名>/cover.jpg       封面图
#   <号名>/<号名>_YYYYMM[-YYYYMM].epub
XHTML_SUBDIR = "xhtml"
COVER_NAME = "cover.jpg"
RAW_SUBDIR = "原始HTML"          # 转换后的原 HTML 备份到这里
INBOX_DIR = ROOT / "_待处理"      # 待处理 HTML 的收件箱（下划线开头，扫号目录时自动跳过）

SKIP_DIRS = {"EPUB成品", ".workbuddy", "微信公众号下载"}  # 源文件夹不当作号目录
BACKUP_HINT = "旧版备份"

# 微信文末推广图黑名单：规则与记账在 promo.py（会自己学），这里只做转发。
# 出厂预置 8 个戴老板固定推广图（视觉确认过）；之后跨文章重复出现在文末的图会自动升为
# 待确认候选，`./epub.sh ads` 看名单，确认后 `./epub.sh ads --promote-all` 永久删除。
PROMO_FILE_IDS = tuple(PROMO.PRESET)

# 图书作者 = 公众号博主；排序作者统一为「沪上陈少」（陈少指定的书架排序名）
SORT_AUTHOR = "沪上陈少"

DATE_RE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日[_\s]*(.*)$")
DATE_IN_TEXT_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")

CSS = """body {
  margin: 0 4%;
  line-height: 1.8;
  font-family: "Songti SC", "SimSun", Georgia, serif;
}
p {
  margin: 0.7em 0;
  text-align: justify;
  text-indent: 0;
}
h1.month {
  font-size: 1.7em;
  font-weight: bold;
  text-align: center;
  margin: 0 0 1.2em;
  padding-bottom: 0.4em;
  border-bottom: 1px solid #ccc;
}
h2.article-title {
  font-size: 1.3em;
  font-weight: bold;
  margin: 1.8em 0 0.3em;
  text-align: left;
  border: none;
}
h3 { font-size: 1.1em; margin: 1.2em 0 0.4em; }
h4 { font-size: 1em; margin: 1em 0 0.3em; }
p.article-meta {
  color: #888;
  font-size: 0.78em;
  text-align: center;
  margin: 0 0 1.4em;
}
img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em auto;
}
p.spacer, div.spacer { height: 0.7em; margin: 0; }
blockquote { margin: 0.8em 1em; color: #555; }
a { color: #0645ad; }
"""


# ---------------------------------------------------------------- 工具
def log(*a):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


def local(tag: str):
    return etree.SubElement if False else tag


def lname(node) -> str:
    """去掉命名空间的标签名。"""
    t = node.tag
    if not isinstance(t, str):
        return ""
    return t.split("}")[-1]


def elem(parent, tag: str, text: str = None, attrib: dict = None, **attrs):
    merged = dict(attrib or {})
    merged.update(attrs)
    node = etree.SubElement(parent, f"{{{XHTML_NS}}}{tag}", attrib=merged or None)
    if text:
        node.text = text
    return node


def safe_stem(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .") or "未命名"


# ---------------------------------------------------------------- 解析单篇
# 超过这个长度就不是标题，是被引擎误判成标题的正文段落
MAX_HEADING_LEN = 40

# 转换器漏判的序号小标题。微信同一公众号的文章模板不统一：
# 有的写 <h5>01</h5>（能识别），有的写 <section>01</section> / <p>03</p>（纯文本，判不出来）。
# 只对「独立成块 + 无图片 + 无子块 + 序号开头 + 简短」的块提升为 h3。
HEADING_SPLIT_RE = re.compile(
    # 数字后紧跟数字或「年」= 年份（90年代 / 2019年），不是序号
    r'^(0?\d{1,2}(?![0-9年])|[一二三四五六七八九十]{1,3}'
    r'|第[一二三四五六七八九十百零\d]{1,4}[章节部篇回]'
    r'|其[一二三四五六七八九十])\s*[.、．:：]?\s*(.*)$'
)
# 序号后面的标题短语：太长的多半是正文里的编号列表（如「1、各国政府赤字率高，…」）
MAX_HEADING_TAIL = 14
SENTENCE_ENDINGS = "。！？；!?"


def looks_like_heading(text: str) -> bool:
    """判断一个纯文本块是不是序号小标题（01 / 01. 信念之芯 / 第一章 / 其一）。"""
    if not text or len(text) > MAX_HEADING_LEN:
        return False
    if text.startswith("["):          # [6] 参考文献条目
        return False
    m = HEADING_SPLIT_RE.match(text)
    if not m:
        return False
    tail = m.group(2).strip()
    if len(tail) > MAX_HEADING_TAIL:
        return False
    # 带句读的是完整句子，不是标题
    if any(c in tail for c in SENTENCE_ENDINGS) or any(c in tail for c in "，,"):
        return False
    return True
BLOCK_TAGS = {"p", "div", "section", "article", "blockquote", "ul", "ol", "li",
              "table", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}


def normalize_headings(nodes: list) -> int:
    """把正文块的小标题统一成 h3，返回真正改动的块数。

    parse_article（在内存里改，供合订）和 tidy_sources（写回源文件）共用这一份判定。
    两边必须走同一套规则，否则会出现「EPUB 目录里有 01/02/03，源文件里却是 <p>」这种
    源文件与成品对不上的情况。

    三步：
      1. 图注不进目录——紧跟图片之后的短文本块打内部标记；
      2. h1/h2 统一成 h3；空的、超长的、以 `[` 开头的（参考文献）、图注，一律降回 <p>；
      3. 补救提升——转换器漏判的序号小标题（纯文本块 `<p>01</p>`）抬成 h3。
    """
    changed = 0

    # 1) 图注不进目录：紧跟在图片块之后的短文本块（如「90年代广州街景」）
    prev_has_img = False
    for node in nodes:
        has_img = bool(node.xpath('.//*[local-name()="img"]'))
        if prev_has_img and not has_img:
            text = "".join(node.itertext()).strip()
            if text and len(text) <= 20 and not looks_like_heading(text):
                node.set("data-caption", "1")
        prev_has_img = has_img

    # 2) h1/h2 → h3，不合格的降回 <p>
    for node in nodes:
        for sub in node.iter():
            if lname(sub) not in ("h1", "h2"):
                continue
            text = "".join(sub.itertext()).strip()
            is_caption = bool(sub.xpath('ancestor-or-self::*[@data-caption]'))
            new = "p" if (not text or len(text) > MAX_HEADING_LEN
                          or text.startswith("[") or is_caption) else "h3"
            if lname(sub) != new:
                sub.tag = f"{{{XHTML_NS}}}{new}"
                changed += 1

    # 3) 补救：转换器漏判的序号小标题（纯文本块，没有 h 标签）
    for node in list(nodes):
        for sub in list(node.iter()):
            if lname(sub) not in ("p", "section", "div"):
                continue
            if any(lname(c) in BLOCK_TAGS for c in sub):
                continue
            if sub.xpath('.//*[local-name()="img"]'):
                continue
            if sub.xpath('ancestor-or-self::*[@data-caption]'):
                continue
            if looks_like_heading("".join(sub.itertext()).strip()):
                sub.tag = f"{{{XHTML_NS}}}h3"
                changed += 1

    # 4) 清掉内部标记，别漏进 EPUB，也别留在源文件里
    for node in nodes:
        for sub in list(node.iter()):
            if sub.get("data-caption") is not None:
                del sub.attrib["data-caption"]

    return changed


class Article:
    __slots__ = ("path", "date", "title", "account", "author", "nodes", "subsections")

    def __init__(self, path, date, title, account, author, nodes):
        self.path = path
        self.date = date          # (y, m, d) 或 None
        self.title = title
        self.account = account
        self.author = author
        self.nodes = nodes        # 正文节点列表（已去掉原标题 h1）
        self.subsections = []     # [(id, 小标题)]，进入三级目录

    @property
    def heading(self) -> str:
        """带日期的标题，例如「2026年8月21日：死贵死贵的」。"""
        if self.date:
            return "%d年%d月%d日：%s" % (self.date[0], self.date[1], self.date[2], self.title)
        return self.title

    @property
    def month_key(self) -> str:
        if self.date:
            return "%04d-%02d" % (self.date[0], self.date[1])
        return "0000-00"

    @property
    def month_label(self) -> str:
        if self.date:
            return "%d年%d月" % (self.date[0], self.date[1])
        return "未标注日期"

    @property
    def sort_key(self):
        return (self.date or (9999, 99, 99), self.title)


def parse_meta_row(text: str):
    """从 meta 行取账号/作者/日期。

    meta 形如「原创 · moomoocat · 猫笔刀 · 2026年8月21日 22:26 · 新加坡」
    或「原创 · 李墨天 · 饭统戴老板 · …」或「原创 · 饭统戴老板 · …」（无作者）。
    词序：作者在前、号名在后；只有一个词时视为号名；IP 属地（第三词以后）忽略。
    """
    parts = [p.strip() for p in (text or "").split("·") if p.strip()]
    names = [
        p for p in parts
        if p not in ("原创", "转载")
        and not DATE_IN_TEXT_RE.search(p)
        and not re.fullmatch(r"[\d:年月日\s]+", p)
    ]
    author = names[0] if len(names) >= 2 else ""
    account = names[1] if len(names) >= 2 else (names[0] if names else "")
    date = None
    for p in parts:
        m = DATE_IN_TEXT_RE.search(p)
        if m:
            date = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            break
    return account, author, date


def parse_article(path: Path) -> Article | None:
    try:
        tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True))
    except Exception as exc:
        log("  解析失败，跳过：", path.name, "->", exc)
        return None
    root = tree.getroot()
    body = root.find(f"{{{XHTML_NS}}}body")
    if body is None:
        log("  无 body，跳过：", path.name)
        return None

    title = ""
    date = None
    meta_text = ""
    nodes = []
    for child in list(body):
        tag = lname(child)
        if tag == "h1" and not nodes and not title:
            title = "".join(child.itertext()).strip()
            continue
        if tag == "p" and (child.get("class") or "") == "article-meta":
            meta_text = "".join(child.itertext()).strip()
            continue
        nodes.append(child)

    # 标题里的日期前缀：2026年8月21日_死贵死贵的
    m = DATE_RE.match(title)
    if m:
        date = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        title = m.group(4).strip() or title

    m_account, m_author, m_date = parse_meta_row(meta_text)
    if date is None:
        date = m_date
    if not title:
        title = path.stem.replace("-Sigil", "")

    # 账号/作者：4 段式文件名（日期_号_作者_标题）优先；
    # 3 段式（日期_作者_标题，旧 EPUB 拆出的）不能从文件名取号名，回退 meta 行。
    account = author = ""
    stem = path.stem.replace("-Sigil", "")
    parts = stem.split("_")
    if len(parts) >= 4 and DATE_IN_TEXT_RE.match(parts[0]):
        account = parts[1]
        author = parts[2]
    if not account:
        account = m_account
    if not author:
        author = m_author

    # 小标题规范化：与 tidy_sources 共用同一套判定，
    # 保证「源文件在 Sigil 里看到的」和「合订出的 EPUB 目录」完全一致。
    normalize_headings(nodes)

    return Article(path, date, title, account, author, nodes)


# ---------------------------------------------------------------- 识别博主
def strip_promo_from_html(src: Path) -> tuple[Path, int, list]:
    """转换前按黑名单删除微信文末推广图，返回 (处理后的临时文件, 删除数, 本文图片清单)。

    只删 img 本身，不动周边文字；源文件不动。没有命中时原样返回原路径。
    顺手把正文所有图片登记一份（fileid / 是否文末），交给 promo.py 累积判断哪些是推广图。
    """
    from lxml import html as LH
    raw = src.read_bytes()
    tree = LH.fromstring(raw, parser=LH.HTMLParser(encoding="utf-8"))
    # 微信 HTML 里有 <o:p> 等 Office 命名空间残留，重新序列化会变成非法标签
    # （下游 etree.QName 直接抛 ValueError），先清掉。
    junk = [el for el in tree.iter()
            if isinstance(el.tag, str) and ":" in el.tag and not el.tag.startswith("{")]
    for el in junk:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
    entries = PROMO.scan_entries(tree)
    active = PROMO.active_ids()
    removed = 0
    for img in tree.xpath('//*[@id="js_content"]//*[local-name()="img"]'):
        u = (img.get("data-src") or img.get("src") or "")
        if any(fid in u for fid in active):
            parent = img.getparent()
            if parent is not None:
                parent.remove(img)
                removed += 1
    if not removed:
        return src, 0, entries
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    with open(tmp, "wb") as f:
        f.write(etree.tostring(tree, method="html", encoding="utf-8"))
    return Path(tmp), removed, entries


def detect_account_from_html(path: Path) -> str:
    """给未转换的微信 HTML，用 sigi_convert 的字段取公众号名。"""
    try:
        sys.path.insert(0, str(SIGI_DIR))
        import sigi_convert as S
        from lxml import html as LH
        src = LH.fromstring(path.read_bytes(), parser=LH.HTMLParser(encoding="utf-8"))
        for eid in ("js_name", "js_author_name_text", "js_author_name"):
            val = S._first_text_by_id(src, eid)
            if val:
                return val.strip()
    except Exception as exc:
        log("  识别公众号失败：", exc)
    return ""


def detect_account_from_xhtml(path: Path) -> str:
    art = parse_article(path)
    return art.account if art else ""


# ---------------------------------------------------------------- 构建
def build_month_xhtml(label: str, articles: list[Article], anchor_ids: dict[int, str]):
    root = etree.Element(f"{{{XHTML_NS}}}html", nsmap=NSMAP)
    head = elem(root, "head")
    elem(head, "title", text=label)
    link = etree.SubElement(head, f"{{{XHTML_NS}}}link",
                            attrib={"rel": "stylesheet", "type": "text/css", "href": "../Styles/main.css"})
    body = elem(root, "body")
    elem(body, "h1", text=label, attrib={"class": "month", "id": "top"})
    for art in articles:
        sec = elem(body, "section")
        date_label = "%d年%d月%d日" % art.date if art.date else ""
        heading = "%s：%s" % (date_label, art.title) if date_label else art.title
        elem(sec, "h2", text=heading,
             attrib={"class": "article-title", "id": anchor_ids[id(art)]})
        if art.author:
            elem(sec, "p", text=art.author, attrib={"class": "article-meta"})
        for node in art.nodes:
            sec.append(node)
    return root


def build_nav(months: list[tuple[str, str, list[Article]]], anchor_ids: dict[int, str],
              book_title: str, file_of_month: dict[str, str]):
    root = etree.Element(f"{{{XHTML_NS}}}html", nsmap=NSMAP)
    head = elem(root, "head")
    elem(head, "title", text="目录")
    etree.SubElement(head, f"{{{XHTML_NS}}}link",
                     attrib={"rel": "stylesheet", "type": "text/css", "href": "../Styles/main.css"})
    body = elem(root, "body")
    nav = etree.SubElement(body, f"{{{XHTML_NS}}}nav",
                           attrib={f"{{{EPUB_NS}}}type": "toc", "id": "toc"})
    elem(nav, "h1", text=book_title)
    ol = elem(nav, "ol")
    for month_key, label, arts in months:
        li = elem(ol, "li")
        elem(li, "a", text=label, attrib={"href": file_of_month[month_key]})
        sub = elem(li, "ol")
        for art in arts:
            sli = elem(sub, "li")
            elem(sli, "a", text=art.heading,
                 attrib={"href": "%s#%s" % (file_of_month[month_key], anchor_ids[id(art)])})
            if art.subsections:
                third = elem(sli, "ol")
                for sid, text in art.subsections:
                    elem(elem(third, "li"), "a", text=text,
                         attrib={"href": "%s#%s" % (file_of_month[month_key], sid)})
    return root


def build_ncx(months, anchor_ids, book_title, uid, file_of_month):
    ncx = etree.Element("{http://www.daisy.org/z3986/2005/ncx/}ncx",
                        nsmap={None: "http://www.daisy.org/z3986/2005/ncx/"},
                        attrib={"version": "2005-1"})
    head = etree.SubElement(ncx, "head")
    etree.SubElement(head, "meta", attrib={"name": "dtb:uid", "content": uid})
    doc_title = etree.SubElement(ncx, "docTitle")
    etree.SubElement(doc_title, "text").text = book_title
    navmap = etree.SubElement(ncx, "navMap")
    order = [0]

    def add_point(parent, label, src):
        order[0] += 1
        point = etree.SubElement(parent, "navPoint",
                                 attrib={"id": "np%04d" % order[0], "playOrder": str(order[0])})
        lbl = etree.SubElement(point, "navLabel")
        etree.SubElement(lbl, "text").text = label
        etree.SubElement(point, "content", attrib={"src": src})
        return point

    for month_key, label, arts in months:
        point = add_point(navmap, label, file_of_month[month_key])
        for art in arts:
            art_point = add_point(point, art.heading,
                                  "%s#%s" % (file_of_month[month_key], anchor_ids[id(art)]))
            for sid, text in art.subsections:
                add_point(art_point, text,
                          "%s#%s" % (file_of_month[month_key], sid))
    return ncx


def build_opf(book_title: str, author: str, uid: str, items: list[tuple[str, str, str]],
              spine_ids: list[str], modified: str, cover_id: str = ""):
    OPF = "http://www.idpf.org/2007/opf"
    DC = "http://purl.org/dc/elements/1.1/"
    opf = etree.Element("{%s}package" % OPF,
                        nsmap={None: OPF, "dc": DC, "opf": OPF},
                        attrib={"version": "3.0", "unique-identifier": "bookid"})
    meta = etree.SubElement(opf, "{%s}metadata" % OPF)
    etree.SubElement(meta, "{%s}identifier" % DC, attrib={"id": "bookid"}).text = uid
    etree.SubElement(meta, "{%s}title" % DC).text = book_title
    if author:
        creator = etree.SubElement(meta, "{%s}creator" % DC, attrib={"id": "creator"})
        creator.text = author
        # EPUB2 阅读器/calibre 读 opf:file-as，EPUB3 读 refines meta，两者都写保证兼容
        creator.set("{%s}file-as" % OPF, SORT_AUTHOR)
        sort_meta = etree.SubElement(meta, "{%s}meta" % OPF,
                                     attrib={"refines": "#creator", "property": "file-as"})
        sort_meta.text = SORT_AUTHOR
    etree.SubElement(meta, "{http://purl.org/dc/elements/1.1/}language").text = "zh-CN"
    m = etree.SubElement(meta, "{http://www.idpf.org/2007/opf}meta",
                         attrib={"property": "dcterms:modified"})
    m.text = modified
    if cover_id:
        etree.SubElement(meta, "{http://www.idpf.org/2007/opf}meta",
                         attrib={"name": "cover", "content": cover_id})

    manifest = etree.SubElement(opf, "{http://www.idpf.org/2007/opf}manifest")
    for item_id, href, media in items:
        attrib = {"id": item_id, "href": href, "media-type": media}
        if item_id == "nav":
            attrib["properties"] = "nav"
        elif item_id == "cover-image":
            attrib["properties"] = "cover-image"
        etree.SubElement(manifest, "{http://www.idpf.org/2007/opf}item", attrib=attrib)
    spine = etree.SubElement(opf, "{http://www.idpf.org/2007/opf}spine", attrib={"toc": "ncx"})
    for sid in spine_ids:
        etree.SubElement(spine, "{http://www.idpf.org/2007/opf}itemref", attrib={"idref": sid})
    return opf


def years_in(book_dir: Path) -> list[int]:
    """该号源文件覆盖了哪些年份（决定是否分册）。"""
    years = set()
    for f in source_dir(book_dir).glob("*.xhtml"):
        m = DATE_RE.match(f.stem.replace("-Sigil", ""))
        if m:
            years.add(int(m.group(1)))
    return sorted(years)


def build_all(account: str, book_dir: Path, split_year: bool = False) -> list[Path]:
    """出一本还是按年出多本。split_year=True 时每年一本（号名_2025.epub、号名_2026.epub）。"""
    if not split_year:
        one = build_book(account, book_dir)
        return [one] if one else []
    outs = []
    for year in years_in(book_dir):
        out = build_book(account, book_dir, year=year)
        if out:
            outs.append(out)
    return outs


def build_book(account: str, book_dir: Path, year: int | None = None) -> Path | None:
    """book_dir 就是该号的独立文件夹；原始文档在 xhtml/ 里，产物写回文件夹根。

    year 指定时只收该年的月份（用于按年分册）。
    """
    src_dir = source_dir(book_dir)
    out_dir = book_dir
    # 先把源 xhtml 里误判的标题降级，保证源文件与合订 EPUB 一致
    tidy_sources(book_dir)
    files = sorted(p for p in src_dir.glob("*.xhtml") if p.is_file())
    arts: list[Article] = []
    for p in files:
        art = parse_article(p)
        if art:
            arts.append(art)
    if not arts:
        log("  %s：没有可用文章，跳过" % account)
        return None

    arts.sort(key=lambda a: a.sort_key)

    # 按月分组，保持从旧到新
    months: list[tuple[str, str, list[Article]]] = []
    for art in arts:
        if year is not None and not art.month_key.startswith(str(year)):
            continue
        if not months or months[-1][0] != art.month_key:
            months.append((art.month_key, art.month_label, []))
        months[-1][2].append(art)
    if not months:
        log("  %s：%s 年没有文章，跳过" % (account, year))
        return None

    anchor_ids: dict[int, str] = {}
    counter = [0]
    for _, _, group in months:
        for art in group:
            counter[0] += 1
            anchor_ids[id(art)] = "art%04d" % counter[0]
            # 文内 h3 小标题：分配唯一 id 并登记进三级目录
            for node in art.nodes:
                for sub in node.iter():
                    if lname(sub) == "h3":
                        text = "".join(sub.itertext()).strip()
                        if not text or len(text) > MAX_HEADING_LEN:
                            continue
                        counter[0] += 1
                        sid = "sec%04d" % counter[0]
                        sub.set("id", sid)
                        art.subsections.append((sid, text))

    file_of_month: dict[str, str] = {}
    for i, (key, _, _) in enumerate(months, 1):
        file_of_month[key] = "Text/part%03d.xhtml" % i

    dates = [a.date for a in arts if a.date]
    if dates:
        span = "%d年%d月-%d年%d月" % (dates[0][0], dates[0][1], dates[-1][0], dates[-1][1])
    else:
        span = ""
    book_title = (account + " " + span).strip() if span else account
    author = account  # 图书作者 = 公众号博主，不是文章里的笔名（如 moomoocat）

    out_dir.mkdir(parents=True, exist_ok=True)
    # 沿用已有命名习惯：202608_猫刀笔.epub / 202608-202609_猫刀笔.epub
    if dates:
        first_tag = "%04d%02d" % (dates[0][0], dates[0][1])
        last_tag = "%04d%02d" % (dates[-1][0], dates[-1][1])
        prefix = first_tag if first_tag == last_tag else "%s-%s" % (first_tag, last_tag)
    else:
        prefix = "合集"
    out_path = out_dir / ("%s_%s.epub" % (safe_stem(account), prefix))
    uid = "urn:uuid:wechat-%s-%s" % (safe_stem(account), dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    modified = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cover = ensure_cover(book_dir, account, span)
    cover_id = ""

    items: list[tuple[str, str, str]] = [
        ("nav", "Text/nav.xhtml", "application/xhtml+xml"),
        ("css", "Styles/main.css", "text/css"),
        ("ncx", "toc.ncx", "application/x-dtbncx+xml"),
    ]
    spine_ids = []

    z = zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED)
    z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    z.writestr("META-INF/container.xml",
               '<?xml version="1.0" encoding="UTF-8"?>\n'
               '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
               '  <rootfiles><rootfile full-path="OEBPS/content.opf" '
               'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')
    z.writestr("OEBPS/Styles/main.css", CSS)

    # 封面：号文件夹里的 cover.jpg 同时嵌进 EPUB
    if cover and cover.exists():
        z.writestr("OEBPS/Images/cover.jpg", cover.read_bytes())
        items.append(("cover-image", "Images/cover.jpg", "image/jpeg"))
        cover_page = (
            "<?xml version='1.0' encoding='utf-8'?>\n<!DOCTYPE html>\n"
            "<html xmlns='http://www.w3.org/1999/xhtml'><head>"
            "<meta charset='utf-8'/><title>封面</title></head>"
            "<body style='margin:0;text-align:center'>"
            "<div><img src='../Images/cover.jpg' alt='封面'/></div>"
            "</body></html>"
        )
        z.writestr("OEBPS/Text/cover.xhtml", cover_page)
        items.append(("cover", "Text/cover.xhtml", "application/xhtml+xml"))
        spine_ids.append("cover")
        cover_id = "cover-image"
    spine_ids.append("nav")  # 封面之后是目录

    for i, (month_key, label, group) in enumerate(months, 1):
        href = file_of_month[month_key]
        root = build_month_xhtml(label, group, anchor_ids)
        z.writestr("OEBPS/" + href,
                   b"<?xml version='1.0' encoding='utf-8'?>\n"
                   + etree.tostring(root, encoding="UTF-8", xml_declaration=False,
                                    doctype='<!DOCTYPE html>'))
        items.append(("p%03d" % i, href, "application/xhtml+xml"))
        spine_ids.append("p%03d" % i)

    nav_root = build_nav(months, anchor_ids, book_title, file_of_month)
    z.writestr("OEBPS/Text/nav.xhtml",
               b"<?xml version='1.0' encoding='utf-8'?>\n"
               + etree.tostring(nav_root, encoding="UTF-8", xml_declaration=False,
                                doctype='<!DOCTYPE html>'))

    opf_root = build_opf(book_title, author, uid, items, spine_ids, modified, cover_id)
    z.writestr("OEBPS/content.opf",
               etree.tostring(opf_root, encoding="UTF-8", xml_declaration=True))

    ncx_root = build_ncx(months, anchor_ids, book_title, uid, file_of_month)
    z.writestr("OEBPS/toc.ncx",
               etree.tostring(ncx_root, encoding="UTF-8", xml_declaration=True))
    z.close()

    size_mb = out_path.stat().st_size / 1024 / 1024
    log("  %s：%d 篇 / %d 个月 → %s（%.1fMB）" % (account, len(arts), len(months), out_path.name, size_mb))
    for month_key, label, group in months:
        log("      %s  %d 篇：%s" % (label, len(group),
                                     "、".join(a.title[:12] for a in group[:4]) + ("…" if len(group) > 4 else "")))
    return out_path


# ---------------------------------------------------------------- 归档新文件
STATS: dict = {"added": [], "skipped": [], "failed": [], "promo": 0,
               "promo_candidates": [], "held": []}

# 成书门槛（陈少 2026-09-16 两次澄清后定）：一个号攒够这么多篇 XHTML 才出书（默认 30）。
# 只管「还没有成品的号」——已有成品的书**不受门槛约束**，照常跟着新增更新。
#   陈少原话：「已经生成的就算了，我说的是后续的默认成书，
#             如果那个文件夹里面不足30篇也需要成书，我会告诉你的」
# 手动点名一律无视门槛（--only / --add / 全量），需要提前出书时用。
# 改门槛：--min-articles N；0 = 关掉门槛。
MIN_PUBLISH = 30


def publish_blocked(book_dir: Path) -> str | None:
    """该号是否因为「篇数没攒够」而暂不成书。返回原因，可成书则返回 None。

    已有成品的书直接放行：否则书会停在旧版本、界面上也看不出异样，没人会发现。
    """
    if MIN_PUBLISH <= 0:
        return None
    if any(book_dir.glob("*.epub")):
        return None
    src = source_dir(book_dir)
    n = len([p for p in src.glob("*.xhtml") if p.is_file()])
    if n >= MIN_PUBLISH:
        return None
    return "源 %d 篇，未达成书门槛 %d 篇（差 %d 篇）" % (n, MIN_PUBLISH, MIN_PUBLISH - n)


def add_files(paths: list[Path], archive_original: bool = False, dry_run=False):
    """归档文件到对应号的 xhtml/ 目录。

    单篇失败不会中断整批：原因记进 STATS["failed"]，最后汇总进处理报告。
    archive_original=True 时，转换完成的原始 HTML 会被移到 <号>/原始HTML/ 备份
    （收件箱流程用，避免同一批文件被重复处理）。
    """
    touched: dict[str, Path] = {}
    for p in paths:
        p = Path(p).expanduser().resolve()
        if not p.is_file():
            log("不存在，跳过：", p)
            continue
        try:
            _add_one(p, archive_original, dry_run, touched)
        except Exception as exc:  # noqa: BLE001 单篇炸了也要把剩下的处理完
            STATS["failed"].append("%s → %s: %s" % (p.name, type(exc).__name__, exc))
            log("  失败，已跳过：", p.name, "->", type(exc).__name__, exc)
    return touched


def _add_one(p: Path, archive_original: bool, dry_run: bool, touched: dict) -> None:
    """处理单个文件：识别号 → 转 XHTML → 归档 → 备份原 HTML。"""
    suffix = p.suffix.lower()
    if suffix in (".html", ".htm"):
        account = detect_account_from_html(p)
        if not account:
            STATS["skipped"].append("%s（认不出公众号）" % p.name)
            log("无法识别公众号，跳过：", p.name)
            return
        dest_dir = resolve_dir(account)
        sys.path.insert(0, str(SIGI_DIR))
        import sigi_convert as S
        from lxml import html as LH
        src = LH.fromstring(p.read_bytes(), parser=LH.HTMLParser(encoding="utf-8"))
        title = S._article_title_from_source(src)
        date_text, base = title.split("_", 1) if "_" in title else ("", title)
        author = S._first_text_by_id(src, "js_author_name") or S._author_from_source(src)
        stem = "_".join(x for x in (date_text, account, author, base) if x)
        target_dir = source_dir(dest_dir)
        dest = target_dir / (S.output_stem_for_title(stem) + "-Sigil.xhtml")
        if dest.exists():
            STATS["skipped"].append("%s（已有同名，内容相同）" % dest.name)
            log("已存在，跳过：", dest.name)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            cleaned, n_promo, img_entries = strip_promo_from_html(p)
            STATS["promo"] += n_promo
            # 记账：本文用到的图片，供 promo.py 判断哪些是跨文章的固定推广图
            for fid in PROMO.record(account, img_entries):
                STATS["promo_candidates"].append(
                    "%s（%s）" % (fid[:16] + "…", account))
            try:
                log("转换：%s → %s/%s/%s" % (
                    p.name, dest_dir.name, XHTML_SUBDIR,
                    "（删推广图 %d 张）" % n_promo if n_promo else ""))
                if not dry_run:
                    S.convert(cleaned, dest, "wechat", False)
                    STATS["added"].append("%s / %s" % (dest_dir.name, dest.name))
            finally:
                if cleaned != p:
                    cleaned.unlink(missing_ok=True)
        if not dry_run and archive_original:
            raw_dir = dest_dir / RAW_SUBDIR
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_dest = raw_dir / p.name
            if raw_dest.exists():
                log("原 HTML 已备份过，删除收件箱副本：", p.name)
                p.unlink()
            else:
                shutil.move(str(p), str(raw_dest))
                log("原 HTML 备份 → %s/%s/" % (dest_dir.name, RAW_SUBDIR))
        touched[dest_dir.name] = dest_dir
    elif suffix == ".xhtml":
        account = detect_account_from_xhtml(p) or p.parent.name
        dest_dir = resolve_dir(account)
        target_dir = source_dir(dest_dir)
        dest = target_dir / p.name
        if dest.exists():
            STATS["skipped"].append("%s（已有同名）" % dest.name)
            log("已存在，跳过：", dest.name)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            log("归档：%s → %s/%s/" % (p.name, dest_dir.name, XHTML_SUBDIR))
            if not dry_run:
                shutil.copy2(p, dest)
                STATS["added"].append("%s / %s" % (dest_dir.name, dest.name))
        touched[dest_dir.name] = dest_dir
    else:
        STATS["skipped"].append("%s（不支持的类型）" % p.name)
        log("不支持的类型，跳过：", p.name)


# ---------------------------------------------------------------- 入口
def resolve_dir(account: str) -> Path:
    """把识别出的号名映射到 ~/Life/EPUB制作 下已存在的目录。

    目录名和文章里的号名可能不完全一致（例如目录「猫刀笔」/ 号名「猫笔刀」），
    直接新建目录会把同一号拆成两本，所以先做同义匹配再考虑新建。
    """
    clean = safe_stem(account)
    direct = ROOT / clean
    if direct.is_dir():
        return direct
    existing = [d for d in collect_book_dirs()]
    if clean:
        for d in existing:
            if d.name == clean:
                return d
        # 字序颠倒或一字之差（猫笔刀 / 猫刀笔）
        for d in existing:
            if sorted(d.name) == sorted(clean):
                log("  号名「%s」匹配到已有目录「%s」" % (clean, d.name))
                return d
        for d in existing:
            if len(d.name) == len(clean) and sum(a != b for a, b in zip(d.name, clean)) <= 1:
                log("  号名「%s」匹配到已有目录「%s」" % (clean, d.name))
                return d
    return direct


def collect_book_dirs():
    """顶层下每个子目录就是一个公众号，彼此隔离。"""
    return sorted(d for d in ROOT.iterdir()
                  if d.is_dir() and not d.name.startswith((".", "_"))
                  and d.name not in SKIP_DIRS and BACKUP_HINT not in d.name)


def source_dir(book_dir: Path) -> Path:
    """该号的原始 XHTML 目录；老结构（xhtml 直接铺在号目录里）也能兼容。"""
    sub = book_dir / XHTML_SUBDIR
    return sub if sub.is_dir() else book_dir


COVER_FONTS = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)


def ensure_cover(book_dir: Path, account: str, span: str) -> Path | None:
    """号文件夹里没有封面时生成一张；已有就原样复用（方便自己换图）。"""
    cover = book_dir / COVER_NAME
    if cover.exists():
        return cover
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log("  未装 Pillow，跳过封面生成")
        return None

    def font(size: int):
        for path in COVER_FONTS:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    W, H = 1200, 1600
    img = Image.new("RGB", (W, H), "#1f2a37")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, H - 300, W, H], fill="#18222e")

    def center(text: str, f, y: int, fill: str):
        box = draw.textbbox((0, 0), text, font=f)
        draw.text(((W - (box[2] - box[0])) / 2 - box[0], y), text, font=f, fill=fill)

    name_font = font(150 if len(account) <= 4 else 110)
    center(account, name_font, 600, "#f4f6f8")
    draw.line([(W / 2 - 90, 800), (W / 2 + 90, 800)], fill="#5b7285", width=3)
    if span:
        center(span, font(46), 850, "#9fb3c4")
    center("沪上陈少 藏", font(40), H - 190, "#7f95a8")

    img.save(cover, "JPEG", quality=88)
    log("  生成封面：", cover.name)
    return cover


def tidy_sources(book_dir: Path) -> int:
    """把源 xhtml 里误判成标题的块降级回正文，让源文件和合订出的 EPUB 保持一致。

    转换引擎（sigi_convert）有时会把参考文献条目、长句子抬成 h1/h2，
    合订时会纠正，但源文件在 Sigil 里单独打开仍是错的。源文件可从
    <号>/原始HTML/ 重新生成，所以就地修正是安全的。

    规范：文章标题 h1（首块），真小标题 h3，其它一律 <p>。
    与 parse_article 的判定保持同一套规则，避免源文件和合订本不一致。
    """
    changed_total = 0
    for path in sorted(source_dir(book_dir).glob("*.xhtml")):
        try:
            tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True))
        except Exception:
            continue
        root = tree.getroot()
        body = root.find(f"{{{XHTML_NS}}}body")
        if body is None:
            continue
        first_h1 = next((el for el in body if lname(el) == "h1"), None)
        # 与 parse_article 一样，把首个 h1（文章标题）和 meta 行排除在正文之外
        nodes = [el for el in body
                 if el is not first_h1 and (el.get("class") or "") != "article-meta"]
        changed = normalize_headings(nodes)
        if changed:
            tree.write(str(path), encoding="UTF-8", xml_declaration=True)
            log("  规范化 %s：%d 处" % (path.name[:40], changed))
            changed_total += changed
    return changed_total


# 浏览器（OpenClaw / SingleFile）存 HTML 的地方，收件箱流程会先去这些目录把新文件吸进来。
# 目录不存在就自动跳过；想加新的源直接往这里加一行。
EXTRA_SOURCES = [
    Path("~/Downloads/微信公众号下载").expanduser(),
    ROOT / "微信公众号下载",
]


def already_archived(name: str) -> bool:
    """该 HTML 是不是已经备份进某号的 原始HTML/ 了。"""
    for book in collect_book_dirs():
        if (book / RAW_SUBDIR / name).exists():
            return True
    return False


def harvest_sources(inbox: Path) -> int:
    """把浏览器存 HTML 的目录（OpenClaw / SingleFile 输出）里的新文件吸进收件箱。

    原文件保留不动；已经在 原始HTML/ 里备份过的直接跳过，所以可以反复跑。
    """
    got = 0
    for src in EXTRA_SOURCES:
        if not src.is_dir():
            continue
        files = [f for f in sorted(src.iterdir())
                 if f.is_file() and f.suffix.lower() in (".html", ".htm")
                 and not f.name.startswith(".")]
        if not files:
            continue
        log("附加源 %s 有 %d 个文件" % (src.name, len(files)))
        for f in files:
            if already_archived(f.name) or (inbox / f.name).exists():
                continue
            shutil.copy2(f, inbox / f.name)
            got += 1
            log("  吸入：", f.name)
    return got


def process_inbox(inbox: Path) -> dict[str, Path]:
    """扫描收件箱：识别博主 → 转 XHTML 归档 → 原始 HTML 备份 → 重建受影响的 EPUB。

    按博主和日期自动分流到各自文件夹，一本 EPUB 只收自己号的文章。
    默认收件箱会先把 EXTRA_SOURCES（浏览器存 HTML 的目录）里的新文件吸进来。
    """
    if not inbox.is_dir():
        log("收件箱不存在，先建一个：", inbox)
        inbox.mkdir(parents=True, exist_ok=True)
        return {}
    if inbox == INBOX_DIR:
        harvest_sources(inbox)
    files = sorted(p for p in inbox.iterdir()
                   if p.is_file() and p.suffix.lower() in (".html", ".htm", ".xhtml")
                   and not p.name.startswith("."))
    if not files:
        log("收件箱是空的：", inbox)
        return {}
    log("收件箱 %s 有 %d 个文件" % (inbox.name, len(files)))
    touched = add_files(files, archive_original=True)
    if not touched:
        return {}
    for account, directory in touched.items():
        why = publish_blocked(directory)
        if why:
            log("  暂不成书：%s（%s）" % (account, why))
            STATS["held"].append("%s：%s" % (account, why))
            continue
        log("重建：", account)
        build_book(account, directory)
    return touched


def epub_stats(epub: Path) -> dict:
    """从成品 EPUB 读出可核对的数字：月份数、文章数、目录条目数、体积。"""
    stats = {"size_mb": round(epub.stat().st_size / 1048576, 1),
             "months": 0, "articles": 0, "toc": 0, "parts": 0}
    try:
        with zipfile.ZipFile(epub) as z:
            names = z.namelist()
            for n in names:
                if "/Text/part" in n:
                    stats["parts"] += 1
                    root = etree.fromstring(z.read(n))
                    stats["months"] += len(root.findall(f".//{{{XHTML_NS}}}h1"))
                    stats["articles"] += len(root.findall(f".//{{{XHTML_NS}}}h2"))
            nav_name = next((n for n in names if n.endswith("nav.xhtml")), None)
            if nav_name:
                nav = etree.fromstring(z.read(nav_name))
                stats["toc"] = len(nav.findall(f".//{{{XHTML_NS}}}li"))
    except Exception as exc:  # noqa: BLE001
        stats["error"] = "%s: %s" % (type(exc).__name__, exc)
    return stats


def book_span(book: Path) -> tuple[str, str, int]:
    """该号 xhtml 的时间跨度与篇数（从文件名日期解析）。"""
    files = sorted(source_dir(book).glob("*.xhtml"))
    keys = []
    for f in files:
        m = DATE_RE.match(f.stem.replace("-Sigil", ""))
        if m:
            keys.append((int(m.group(1)), int(m.group(2))))
    if not keys:
        return "—", "—", len(files)
    keys.sort()
    return ("%04d-%02d" % keys[0], "%04d-%02d" % keys[-1], len(files))


def refresh_ledger() -> Path:
    """刷新 ~/Life/EPUB制作/_台账.md——所有书的一览，随时能看清家底。"""
    path = ROOT / "_台账.md"
    lines = ["# 成书台账", "",
             "由 build_epub.py 自动刷新，别手改（改了会被覆盖）。", "",
             "| 公众号 | 篇数 | 跨度 | 成品 | 体积 | 目录条目 | 最后更新 |",
             "|---|---:|---|---|---:|---:|---|"]
    total = 0
    for book in collect_book_dirs():
        epubs = sorted(book.glob("*.epub"))
        first, last, count = book_span(book)
        why = publish_blocked(book)
        if not epubs:
            note = ("（未成书·差 %d 篇）" % (MIN_PUBLISH - count)) if why else "（未成书）"
            lines.append("| %s | %d | %s ~ %s | %s | — | — | — |"
                         % (book.name, count, first, last, note))
            continue
        total += count
        for i, ep in enumerate(epubs):
            st = epub_stats(ep)
            mtime = dt.datetime.fromtimestamp(ep.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            lines.append("| %s | %s | %s ~ %s | %s | %.1fMB | %d | %s |" % (
                book.name if i == 0 else "", count if i == 0 else "",
                first if i == 0 else "", last if i == 0 else "",
                ep.name, st["size_mb"], st["toc"], mtime))
    lines += ["", "合计 %d 篇。" % total,
              "体检：`cd _engine && ./epub.sh check`　回归：`./epub.sh test`", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_report(built: list[str]) -> Path:
    """把这次运行干了什么写成 _处理报告.md，方便事后回看。"""
    path = ROOT / "_处理报告.md"
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["# 处理报告", "", "- 时间：%s" % now,
             "- 新增归档：%d" % len(STATS["added"]),
             "- 跳过重复：%d" % len(STATS["skipped"]),
             "- 失败：%d" % len(STATS["failed"]),
             "- 暂缓成书：%d（篇数没攒够，等够了自动出书）" % len(STATS["held"]),
             "- 删除推广图：%d 张" % STATS["promo"], ""]
    if STATS["held"]:
        lines += ["## 暂缓成书（%d）" % len(STATS["held"]),
                  "源文件已经归档进 `xhtml/` 了，只是这个号还没成过书、篇数也没到门槛（%d 篇），所以没打包。" % MIN_PUBLISH,
                  "等篇数攒够会自动出书；想现在就要，跑 `./epub.sh only <号名>`（手动点名无视门槛）。", ""]
        lines += ["- %s" % x for x in STATS["held"]]
        lines.append("")
    if STATS["promo_candidates"]:
        lines += ["## 疑似推广图（待确认）",
                  "跨文章重复出现在文末，已列为候选。看过确认后跑 `./epub.sh ads --promote-all`。",
                  ""] + ["- %s" % x for x in STATS["promo_candidates"]] + [""]
    for title, items in (("新增", STATS["added"]), ("跳过", STATS["skipped"]),
                         ("失败", STATS["failed"])):
        if not items:
            continue
        lines.append("## %s（%d）" % (title, len(items)))
        lines += ["- %s" % x for x in items]
        lines.append("")
    if built:
        lines.append("## 重建的书")
        for name in built:
            book = ROOT / name
            epubs = sorted(book.glob("*.epub"))
            for ep in epubs:
                st = epub_stats(ep)
                lines.append("- **%s**：%d 篇 / %d 个月 / 目录 %d 条 / %.1fMB"
                             % (ep.name, st["articles"], st["months"], st["toc"], st["size_mb"]))
        lines.append("")
    if not STATS["added"] and not STATS["failed"]:
        lines.append("（这次没有新增，只是重跑了一遍。）")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    log("处理报告 →", path.name)
    return path


def backfill_ads() -> tuple[int, int]:
    """把已归档的 原始HTML/ 全扫一遍补记账，让黑名单知识库立刻有全量历史。

    只记账不转换、不改任何书；重复跑不会重复计数（record 内部按篇去重）。
    """
    from lxml import html as LH
    n_files = n_cands = 0
    for book in collect_book_dirs():
        raw = book / RAW_SUBDIR
        if not raw.is_dir():
            continue
        for f in sorted(raw.glob("*.htm*")):
            try:
                tree = LH.fromstring(f.read_bytes(), parser=LH.HTMLParser(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log("  跳过（解析失败）：", f.name, "->", exc)
                continue
            cands = PROMO.record(book.name, PROMO.scan_entries(tree))
            n_files += 1
            n_cands += len(cands)
            log("  记账 %s / %s%s" % (book.name, f.name[:36],
                                     "（新候选 %d）" % len(cands) if cands else ""))
    return n_files, n_cands


def finish(built: list[str]) -> None:
    """收尾：写处理报告 + 刷新台账 + 刷新推广图名单 + 重建报表页面。"""
    write_report(built)
    refresh_ledger()
    (ROOT / "_推广图黑名单.md").write_text("\n".join(PROMO.report_lines()), encoding="utf-8")
    log("台账 →", "_台账.md")
    try:  # 报表只是好看，坏了绝不能影响成书
        import report as RP
        accounts = [RP.scan_account(b) for b in collect_book_dirs()]
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        (ROOT / "_报表.html").write_text(RP.build_html(accounts, now), encoding="utf-8")
        log("报表 →", "_报表.html")
    except Exception as exc:  # noqa: BLE001
        log("  报表生成失败（不影响成书）：", exc)


def main() -> int:
    global MIN_PUBLISH
    ap = argparse.ArgumentParser(description="把 Sigil XHTML 合成按月分章的 EPUB")
    ap.add_argument("--only", help="只重建指定公众号（目录名）")
    ap.add_argument("--add", nargs="+", help="归档新文件（.xhtml 或 .html）并重建对应 EPUB")
    ap.add_argument("--inbox", nargs="?", const=str(INBOX_DIR),
                    help="扫描收件箱并分流处理（默认 %s）" % INBOX_DIR)
    ap.add_argument("--list", action="store_true", help="只扫描不打包")
    ap.add_argument("--split-year", action="store_true",
                    help="按年分册（号名_2025.epub、号名_2026.epub），书太大时用")
    ap.add_argument("--backfill-ads", action="store_true",
                    help="把已归档的 原始HTML/ 全扫一遍补记图片账（不转换、不改书）")
    ap.add_argument("--min-articles", type=int, default=MIN_PUBLISH,
                    help="首次成书门槛（源 XHTML 篇数），默认 %d；0 = 不设门槛" % MIN_PUBLISH)
    args = ap.parse_args()

    MIN_PUBLISH = max(0, args.min_articles)

    if args.backfill_ads:
        n_files, n_cands = backfill_ads()
        log("补记账完成：%d 篇，新升候选 %d 个。名单见 ./epub.sh ads" % (n_files, n_cands))
        (ROOT / "_推广图黑名单.md").write_text("\n".join(PROMO.report_lines()), encoding="utf-8")
        return 0

    if args.inbox:
        touched = process_inbox(Path(args.inbox).expanduser().resolve())
        # 只有真的出了书的才算「重建」，被门槛挡下的进报告里的「暂缓成书」
        built = [a for a, d in touched.items() if not publish_blocked(d)]
        finish(built)
        return 0

    if args.add:
        touched = add_files([Path(x) for x in args.add])
        for account, directory in touched.items():
            log("重建：", account)
            build_book(account, directory)
        finish(list(touched))
        return 1 if not touched else 0

    if args.only:
        dirs = [ROOT / args.only]
    else:
        dirs = collect_book_dirs()

    if not dirs:
        log("没有找到公众号目录：", ROOT)
        return 1

    built: list[str] = []
    for d in dirs:
        if not d.is_dir():
            log("目录不存在：", d)
            continue
        if args.list:
            arts = [a for a in (parse_article(p) for p in sorted(source_dir(d).glob("*.xhtml"))) if a]
            arts.sort(key=lambda a: a.sort_key)
            log("%s：%d 篇" % (d.name, len(arts)))
            for a in arts:
                log("   ", a.month_label, "|", a.title)
            continue
        log("构建：", d.name)
        if build_all(d.name, d, split_year=args.split_year):
            built.append(d.name)
    finish(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
