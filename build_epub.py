#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sigil XHTML → 合订 EPUB（按公众号分本，按月分章）

本项目位置（2026-09-26 学习工厂搬迁后定案，长期不变）
    代码  ~/Life/学习工厂/02、文稿成品线/_engine/   本文件所在目录
    数据  ~/Life/学习工厂/02、文稿成品线/02、定稿库/01、微信公众号/<公众号名>/
                                       xhtml/ · 原始HTML/ · cover.jpg · *.epub
    投递箱 ~/Life/学习工厂/02、文稿成品线/01、投递箱/

目录约定（号目录根由 _books_root() 按迁移标记决定）
    <号目录根>/<公众号名>/              该号所有 -Sigil.xhtml

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

去重规则（陈少 2026-10-11 定，全部号生效）
    投递箱里某 HTML 对应的 xhtml 已存在（标题相同 / 已有同名 xhtml）→ 不再处理，
    直接丢系统废纸篓（可恢复），并在 _处理报告.md 与控制台明确告知。
    想用重下的 HTML 主动替换已有 xhtml：跑时加 --force（陈少发「强制处理」时）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
import subprocess
import zipfile
from pathlib import Path

import base64
import io
from PIL import Image

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promo as PROMO  # noqa: E402
import emoji_tag  # noqa: E402
import kindle_check  # noqa: E402  Kindle 兼容性校验（出书后自动跑）
import kindle_fix_epub  # noqa: E402  不达标自动修（WebP 转 JPEG 等），修不了才拦下

XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
NSMAP = {None: XHTML_NS, "epub": EPUB_NS}

ENGINE_DIR = Path(__file__).resolve().parent          # _engine/（代码所在目录）
ROOT = ENGINE_DIR.parent                              # ~/Life/学习工厂/02、文稿成品线（线根）
# 号目录实际在 ROOT/02、定稿库/01、微信公众号/ 下，由 _books_root() 决定
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
""" + "\n" + emoji_tag.EMOJI_CSS_RULE


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
    # "%" 也要换掉：libxml2 写文件时把路径当 URI，"%25" 会被再编码出一层，
    # 读写不对称会让同一个文件越写越多份（2026-09-16 踩过，详见 sigi_convert）
    return re.sub(r'[\\/:*?"<>|]+', "_", name).replace("%", "％").strip(" .") or "未命名"


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


# 表情/贴图标记统一走 emoji_tag 共享模块（判定与样式唯一来源，避免多处漂移）。
# build 期这层是**兜底**：源 xhtml 通常已在转换时打好标，这里保证手改/外部来源的
# xhtml 也不会漏。详见 emoji_tag.py。
EMOJI_MAX_PX = emoji_tag.EMOJI_MAX_PX


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
    # 小图（表情/贴图）加 class="emoji"（幂等；源 xhtml 转换时通常已打好，这里兜底）。
    emoji_tag.tag_small_images(nodes)

    return Article(path, date, title, account, author, nodes)


# ---------------------------------------------------------------- 识别博主
def strip_promo_from_html(src: Path) -> tuple[Path, int, list]:
    """转换前按黑名单删除微信文末推广图，返回 (处理后的临时文件, 删除数, 本文图片清单)。

    只删 img 本身，不动周边文字；源文件不动。没有命中时原样返回原路径。
    顺手把正文所有图片登记一份（fileid / 是否文末），交给 promo.py 累积判断哪些是推广图。
    """
    from lxml import html as LH
    raw = src.read_bytes()
    tree = LH.fromstring(raw, parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
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


# 公众号 biz（唯一 ID）。老快照常常没有 js_name，但 biz 一定在——
# 号名能改，biz 不能，所以它比号名可靠。2026-09-17 靠它救回 14 篇"未识别"的晚点文章。
_BIZ_RE = re.compile(r'biz:\s*""\s*\|\|\s*"([A-Za-z0-9=+/]+)"')
_BIZ_URL_RE = re.compile(r"__biz=([A-Za-z0-9=+/]+)")
BIZ_MAP_FILE = ROOT / "_号biz映射.json"


def biz_of(path: Path) -> str:
    """抽 biz。只读前 300KB——biz 一定在 head 里，没必要读整个几十 MB 的快照。"""
    try:
        with open(path, "rb") as fh:
            text = fh.read(300_000).decode("utf-8", "ignore")
    except Exception:
        return ""
    m = _BIZ_RE.search(text) or _BIZ_URL_RE.search(text)
    return m.group(1) if m else ""


def load_biz_map() -> dict:
    """{biz: 号名}。持久化在 _号biz映射.json——原 HTML 删掉之后就没法学了，必须落盘。"""
    try:
        if BIZ_MAP_FILE.exists():
            data = json.loads(BIZ_MAP_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def build_biz_map() -> dict:
    """从各号 原始HTML/ 学出 {biz: 号名}，与已有表合并后落盘。

    只在还有原始 HTML 的时候能学——所以删原件之前必须跑一次。
    """
    out = load_biz_map()
    for book in collect_book_dirs():
        raw = book / RAW_SUBDIR
        if not raw.is_dir():
            continue
        for f in raw.rglob("*.htm*"):
            b = biz_of(f)
            if b:
                out.setdefault(b, book.name)
    save_biz_map(out)
    return out


def save_biz_map(mapping: dict) -> None:
    try:
        BIZ_MAP_FILE.write_text(json.dumps(mapping, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    except OSError as exc:
        log("biz 映射表保存失败：", exc)


def register_biz(path: Path, account: str) -> None:
    """归档时把这个文件的 biz 记到映射表里，以后同号的老快照也能认出来。"""
    b = biz_of(path)
    if not b or not account:
        return
    m = load_biz_map()
    if m.get(b) != account:
        m[b] = account
        save_biz_map(m)


def detect_account_from_html(path: Path) -> str:
    """给未转换的微信 HTML 定公众号名。

    顺序：js_name（最直接）→ Notion 属性表/biz 映射（老快照与 Notion 副本靠它）
          → 空（交给调用方兜底）。
    """
    try:
        sys.path.insert(0, str(SIGI_DIR))
        import sigi_convert as S
        from lxml import html as LH
        src = LH.fromstring(path.read_bytes(), parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
        for eid in ("js_name", "js_author_name_text", "js_author_name"):
            val = S._first_text_by_id(src, eid)
            if val:
                return val.strip()
        # Notion 导出：号名用 biz 反查映射表（拿不到再退回属性表「作者」）。
        # 判据是"属性表里有 来源/类型"，而不是看文件长什么样——
        # 微信直取的快照里也可能有 var ct 之类的东西，硬套会拿错号。
        if S._notion_property(src, "来源"):
            return (S._notion_account_name(src)
                    or S._notion_property(src, "作者").strip())
    except Exception as exc:
        log("  识别公众号失败：", exc)
    return load_biz_map().get(biz_of(path), "")


def detect_account_from_xhtml(path: Path) -> str:
    art = parse_article(path)
    return art.account if art else ""


# ---------------------------------------------------------------- 构建

# 图片外置（Kindle 兼容，陈少 2026-10-13 定）
# Kindle 的 EPUB→KFX 转换不支持内联 base64 图，且完全不支持 WebP —— 直接隐藏。
# 出书时把所有 data URI 图抽成 OEBPS/Images/ 外部资源（WebP/GIF→JPEG，PNG/JPEG 原样保留），
# 源 xhtml 仍保持内联（Sigil 里照常看）。每本 EPUB 用 _IMG_REGISTRY 去重复用。
_IMG_REGISTRY: dict = {}


def _decode_data_uri(uri: str):
    m = re.match(r"data:([^;]+);base64,(.*)$", uri, re.S)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None
    return m.group(1).lower(), raw


def _data_uri_to_image(uri: str, z: zipfile.ZipFile):
    dec = _decode_data_uri(uri)
    if not dec:
        return None
    mime, raw = dec
    h = hashlib.sha1(raw).hexdigest()
    if h in _IMG_REGISTRY:
        return _IMG_REGISTRY[h]
    ext = mt = None
    out = None
    try:
        im = Image.open(io.BytesIO(raw))
        fmt = (im.format or "").upper()
    except Exception:
        im, fmt = None, ""
    if mime == "image/webp" or fmt == "WEBP":
        im = im.convert("RGBA") if im.mode in ("RGBA", "LA") else im.convert("RGB")
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
        out, ext, mt = buf.getvalue(), "jpg", "image/jpeg"
    elif mime == "image/gif" or fmt == "GIF":
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        buf = io.BytesIO()
        bg.save(buf, "JPEG", quality=85)
        out, ext, mt = buf.getvalue(), "jpg", "image/jpeg"
    elif mime == "image/png" or fmt == "PNG":
        out, ext, mt = raw, "png", "image/png"   # PNG：Kindle 支持，原样保留
    else:
        out, ext, mt = raw, ("jpg" if mime == "image/jpeg" else "png"), \
            (mime if mime.startswith("image/") else "image/jpeg")
    img_id = "img%04d" % (len(_IMG_REGISTRY) + 1)
    fname = "Images/%s.%s" % (img_id, ext)
    z.writestr("OEBPS/" + fname, out)
    entry = (img_id, fname, mt)
    _IMG_REGISTRY[h] = entry
    return entry


def externalize_images(root, z: zipfile.ZipFile):
    """遍历 part 里的 <img>，把 data URI 图外置为 EPUB 内部资源并改写 src。"""
    for el in root.iter():
        if lname(el) != "img":
            continue
        src = el.get("src")
        if not src or not src.startswith("data:"):
            continue
        entry = _data_uri_to_image(src, z)
        if entry:
            el.set("src", "../" + entry[1])


def build_month_xhtml(label: str, articles: list[Article], anchor_ids: dict[int, str],
                      single: bool = False):
    root = etree.Element(f"{{{XHTML_NS}}}html", nsmap=NSMAP)
    head = elem(root, "head")
    elem(head, "title", text=label)
    link = etree.SubElement(head, f"{{{XHTML_NS}}}link",
                            attrib={"rel": "stylesheet", "type": "text/css", "href": "../Styles/main.css"})
    body = elem(root, "body")
    if not single:
        # 单月分册本不重复显示月份（文件名/封面已标），文章直接顶上来
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
              book_title: str, file_of_month: dict[str, str], single: bool = False):
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
    if single:
        # 单月分册本：压平月份层，文章标题直接列在书名下
        month_key, _, arts = months[0]
        for art in arts:
            sli = elem(ol, "li")
            elem(sli, "a", text=art.heading,
                 attrib={"href": "%s#%s" % (file_of_month[month_key], anchor_ids[id(art)])})
            if art.subsections:
                third = elem(sli, "ol")
                for sid, text in art.subsections:
                    elem(elem(third, "li"), "a", text=text,
                         attrib={"href": "%s#%s" % (file_of_month[month_key], sid)})
    else:
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


def build_ncx(months, anchor_ids, book_title, uid, file_of_month, single: bool = False):
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

    if single:
        month_key, _, arts = months[0]
        for art in arts:
            art_point = add_point(navmap, art.heading,
                                  "%s#%s" % (file_of_month[month_key], anchor_ids[id(art)]))
            for sid, text in art.subsections:
                add_point(art_point, text,
                          "%s#%s" % (file_of_month[month_key], sid))
    else:
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
    """出一本 / 按年出多本 / 按月出多本。

    - 该按月分册（公众号点名号，或非公众号线 ≥100 篇）：每月一本
      （号名_202609.epub、号名_202610.epub）
    - split_year=True：每年一本（号名_2025.epub、号名_2026.epub）
    - 否则：单本合订（号名_YYYYMM[-YYYYMM].epub）
    """
    if should_split_by_month(account, book_dir):
        src_dir = source_dir(book_dir)
        keys: set[str] = set()
        for p in sorted(src_dir.glob("*.xhtml")):
            m = DATE_RE.match(p.stem.replace("-Sigil", ""))
            if m:
                keys.add("%04d-%02d" % (int(m.group(1)), int(m.group(2))))
        outs = []
        for k in sorted(keys):
            out = build_book(account, book_dir, month=k)
            if out:
                outs.append(out)
        return outs
    if not split_year:
        one = build_book(account, book_dir)
        return [one] if one else []
    outs = []
    for year in years_in(book_dir):
        out = build_book(account, book_dir, year=year)
        if out:
            outs.append(out)
    return outs


def build_book(account: str, book_dir: Path, year: int | None = None,
              month: str | None = None) -> Path | None:
    """book_dir 就是该号的独立文件夹；原始文档在 xhtml/ 里，产物写回文件夹根。

    year 指定时只收该年的月份（用于按年分册）。
    month 指定时只收该月（"YYYY-MM"，用于按月分册：号名_YYYYMM.epub）。
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
        if month is not None and art.month_key != month:
            continue
        if not months or months[-1][0] != art.month_key:
            months.append((art.month_key, art.month_label, []))
        months[-1][2].append(art)
    if not months:
        if month is not None:
            log("  %s：%s 月没有文章，跳过" % (account, month))
        else:
            log("  %s：%s 年没有文章，跳过" % (account, year))
        return None

    single = len(months) == 1   # 单月书：压平月份层（分册号每本都是单月）
    _IMG_REGISTRY.clear()       # 每本 EPUB 重置图片去重表

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

    dates = [a.date for _, _, grp in months for a in grp if a.date]
    if month is not None and months:
        span = months[0][1]                      # 单月分册："2026年9月"
    elif dates:
        span = "%d年%d月-%d年%d月" % (dates[0][0], dates[0][1], dates[-1][0], dates[-1][1])
    else:
        span = ""
    book_title = (account + " " + span).strip() if span else account
    author = account  # 图书作者 = 公众号博主，不是文章里的笔名（如 moomoocat）

    out_dir.mkdir(parents=True, exist_ok=True)
    # 命名规则（陈少 2026-10-13 定）：
    #   非公众号线（知乎/小红书/微博/Twitter）不分册时 → **号名.epub**（不带月份）
    #     理由：这些线是"一个作者一本"，月份范围对读者没意义，写进文件名反而碍事。
    #   公众号线 → 沿用月份范围（号名_202608.epub / 号名_202608-202609.epub）
    single_no_month = (is_non_wechat_platform(book_dir)
                       and not should_split_by_month(account, book_dir))
    if single_no_month:
        out_path = out_dir / ("%s.epub" % safe_stem(account))
    else:
        if dates:
            first_tag = "%04d%02d" % (dates[0][0], dates[0][1])
            last_tag = "%04d%02d" % (dates[-1][0], dates[-1][1])
            prefix = first_tag if first_tag == last_tag else "%s-%s" % (first_tag, last_tag)
        else:
            prefix = "合集"
        out_path = out_dir / ("%s_%s.epub" % (safe_stem(account), prefix))
    uid = "urn:uuid:wechat-%s-%s" % (safe_stem(account), dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    modified = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cover_id = ""
    platform = platform_label(book_dir)
    if should_split_by_month(account, book_dir):
        # 按月分册（目前只有微信公众号有此情况）：封面带月份
        cover_bytes = make_cover_bytes(account, platform, span)
    else:
        # 一个作者一本：下面不写月份（陈少 2026-10-13：不是整月就不写）
        cover = ensure_cover(book_dir, account, "", force=COVER_REGEN)
        cover_bytes = cover.read_bytes() if (cover and cover.exists()) else None

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

    # 封面：号文件夹里的 cover.jpg（或分册号在内存生成的米白封面）嵌进 EPUB
    if cover_bytes:
        z.writestr("OEBPS/Images/cover.jpg", cover_bytes)
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
        root = build_month_xhtml(label, group, anchor_ids, single=single)
        externalize_images(root, z)   # 内联图外置为 OEBPS/Images/（WebP→JPEG），Kindle 兼容
        z.writestr("OEBPS/" + href,
                   b"<?xml version='1.0' encoding='utf-8'?>\n"
                   + etree.tostring(root, encoding="UTF-8", xml_declaration=False,
                                    doctype='<!DOCTYPE html>'))
        items.append(("p%03d" % i, href, "application/xhtml+xml"))
        spine_ids.append("p%03d" % i)

    items.extend(list(_IMG_REGISTRY.values()))  # 外置图片进 manifest（去重后）

    nav_root = build_nav(months, anchor_ids, book_title, file_of_month, single=single)
    z.writestr("OEBPS/Text/nav.xhtml",
               b"<?xml version='1.0' encoding='utf-8'?>\n"
               + etree.tostring(nav_root, encoding="UTF-8", xml_declaration=False,
                                doctype='<!DOCTYPE html>'))

    opf_root = build_opf(book_title, author, uid, items, spine_ids, modified, cover_id)
    z.writestr("OEBPS/content.opf",
               etree.tostring(opf_root, encoding="UTF-8", xml_declaration=True))

    ncx_root = build_ncx(months, anchor_ids, book_title, uid, file_of_month, single=single)
    z.writestr("OEBPS/toc.ncx",
               etree.tostring(ncx_root, encoding="UTF-8", xml_declaration=True))
    z.close()

    # ------------------------------------------------------------------
    # Kindle/Kobo 阅读器友好硬约束关卡（陈少 2026-10-13 定）
    #   生成阶段已把内联图外置、WebP→JPEG；此处再校验一次，不达标自动修；
    #   修不了（如源图裂、格式太怪）则标红拦下，不再当作合格产物。
    # ------------------------------------------------------------------
    kindle_issues = kindle_check.check_epub_kindle(out_path)
    if kindle_issues:
        log("  ⟳ Kindle 友好校验未通过（%d 项），自动修复中…" % len(kindle_issues))
        try:
            kindle_fix_epub.fix_one(str(out_path), check_only=False, no_backup=True, force=True)
        except Exception as _exc:  # 修复失败不能假装成功
            log("  ✗ 自动修复异常：%s: %s" % (type(_exc).__name__, _exc))
        kindle_issues = kindle_check.check_epub_kindle(out_path)
    if kindle_issues:
        log("  ✗ Kindle 不友好，已拦下（%s）：" % out_path.name)
        for _ki in kindle_issues[:20]:
            log("      " + _ki)
        return None            # 不合格 → 不算成功出书
    log("  ✓ Kindle/Kobo 友好（图片外置、无 WebP、无缺失）")

    size_mb = out_path.stat().st_size / 1024 / 1024
    n_arts = sum(len(g) for _, _, g in months)
    log("  %s：%d 篇 / %d 个月 → %s（%.1fMB）" % (account, n_arts, len(months), out_path.name, size_mb))
    for month_key, label, group in months:
        log("      %s  %d 篇：%s" % (label, len(group),
                                     "、".join(a.title[:12] for a in group[:4]) + ("…" if len(group) > 4 else "")))
    return out_path


# ---------------------------------------------------------------- 归档新文件
STATS: dict = {"added": [], "skipped": [], "failed": [], "promo": 0,
               "promo_candidates": [], "held": [],
               "harvested": [], "non_wechat": [], "dup_fp": [], "cleanable": [],
               "pending_accounts": [], "dup_title": [], "trashed": []}

# 强制处理开关：陈少发「强制处理」时由 --force 置 True，绕过去重、用新 HTML 覆盖重生成 xhtml
FORCE_PROCESS = False


def move_to_trash(p: Path) -> bool:
    """把文件移到系统废纸篓（可恢复），失败回退 ~/.Trash。

    重复 HTML 按陈少 2026-10-11 定：对应 xhtml 已存在时不再处理，直接丢废纸篓
    （绝不硬删，Finder 里可右键「放回原处」）。废纸篓里同名冲突自动加 (n) 后缀。
    """
    p = Path(p)
    if not p.exists():
        return True
    # 优先系统 trash 命令（macOS 废纸篓）
    if Path("/usr/bin/trash").exists():
        r = subprocess.run(["/usr/bin/trash", str(p)], capture_output=True, text=True)
        if r.returncode == 0 and not p.exists():
            return True
    # 回退：移到 ~/.Trash，同名冲突加 (n) 后缀
    trash_dir = Path.home() / ".Trash"
    try:
        trash_dir.mkdir(exist_ok=True)
    except OSError:
        return False
    dest = trash_dir / p.name
    if dest.exists():
        base, ext = p.stem, p.suffix
        i = 1
        while (trash_dir / ("%s (%d)%s" % (base, i, ext))).exists():
            i += 1
        dest = trash_dir / ("%s (%d)%s" % (base, i, ext))
    try:
        shutil.move(str(p), str(dest))
        return True
    except OSError:
        return False

# 认「这是不是微信公众号文章」的两个特征（正文容器 / 微信图床）
WECHAT_MARKS = ('id="js_content"', "mmbiz.qpic.cn")
# 2026-09-17 补：有一类 SingleFile 存档把真正的 DOM 塞在文件后半段，头部 512KB 里
# 只有 meta 和压缩 JS，上面两个特征一个都撞不到（实测 6 篇全被判「不是微信文章」）。
# 头部只要有微信专属的 og:url 就认，不为了它去全量读 19MB。
WECHAT_HEAD_RE = re.compile(r'og:url["\']?\s+content=["\']https?://mp\.weixin\.qq\.com')

# 号白名单：名单外的新号先不建书，只报告等确认（`./epub.sh allow 号名` 加进来）
ALLOW_FILE = ENGINE_DIR / "allowed_accounts.json"
ALLOW_ALL_ACCOUNTS = False


def _load_allowed() -> set:
    if not ALLOW_FILE.exists():
        return set()
    try:
        return set(json.loads(ALLOW_FILE.read_text(encoding="utf-8")).get("allow", []))
    except Exception:  # noqa: BLE001
        return set()


ALLOWED_ACCOUNTS = _load_allowed()

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
    if should_split_by_month(book_dir.name, book_dir):
        return None  # 分册号豁免 30 篇门槛：每月独立出书，单月哪怕 1 篇也出
    # 非公众号线（知乎/小红书/微博/Twitter）豁免 30 篇门槛（陈少 2026-10-13 定：
    # "全量补齐" —— 这些线的规则是一个作者一本，没有攒够篇数才出书的说法）。
    # 公众号保持原门槛不变。
    if is_non_wechat_platform(book_dir):
        return None
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


_OFFICE_TAG_RE = re.compile(r"</?[a-zA-Z]+:[a-zA-Z]+[^>]*>")


def sanitize_office_markup(path: Path) -> int:
    """去掉 Word 粘贴残留的命名空间标签（<o:p>、<w:sdt> 之类）。

    lxml 的 HTML 解析器碰到它们会直接抛 `ValueError: Invalid tag name 'o:p'`，
    整篇文章就转不了（2026-09-17 实测：深网腾讯新闻那篇）。
    这些标签本身没有排版意义，删掉不影响正文。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    cleaned = _OFFICE_TAG_RE.sub("", text)
    if cleaned == text:
        return 0
    try:
        path.write_text(cleaned, encoding="utf-8")
    except OSError as exc:
        log("  Office 标签清理失败：", exc)
        return 0
    return text.count("<o:") + text.count("</o:")


# 转换结果小于这个字节数就当空壳拒收（正常文章再短也有几 KB）
# 空壳判定：**看正文有没有内容**，不看文件多少字节。
#
# 为什么不能用字节数：Twitter/微博的短帖正文可能只有两三百字，
# 转出来不到 3KB，但它不是空壳（2026-10-13 实测：Morris 那三篇被误拦）。
# 真正的空壳是"正文一个字都没有"——那种无论文件多大都是坏的。
#
# 为什么从 80 降到 40：推文可以很短。2026-10-13 实测 Jason 那第三条
# 正文 93 字（完整推文，同作者另两条是 118/155 字），被 80 卡掉过一次。
# 40 字仍能挡住真正的空壳（那种只有 0-10 字），又不会误伤短帖。
MIN_BODY_CHARS = 40


def is_empty_shell(path: Path) -> tuple[bool, int]:
    """转出来的 XHTML 是不是空壳。返回 (是否空壳, 正文字数)。

    正文取 <body> 里的纯文本长度，去掉标题与元数据行。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True, 0
    body = text[text.find("<body"):] if "<body" in text else text
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.S | re.I)
    plain = re.sub(r"<[^>]+>", "", body)
    plain = re.sub(r"\s+", "", plain)
    # 扣掉 h1 标题与 article-meta 那行的字数（它们不算正文）
    title_len = 0
    m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    if m:
        title_len += len(re.sub(r"<[^>]+>", "", m.group(1)).strip())
    m = re.search(r'class="article-meta"[^>]*>(.*?)<', body, re.S)
    if m:
        title_len += len(m.group(1).strip())
    return len(plain) - title_len < MIN_BODY_CHARS, len(plain) - title_len


def _cfg_bool(key: str, default: bool) -> bool:
    """读 core 配置里的开关。core 是基础层，业务用它没问题（反过来才禁止）。"""
    try:
        from core import config
        return bool(config.get(key, default))
    except Exception:
        return default


def _norm_title(t: str) -> str:
    """标题归一化，用来判"是不是同一篇"。

    要去掉两类噪音，否则同一篇会因写法不同被判成两篇（2026-10-13 踩过：
    迁移版标题「去泰国…」与 Notion 版「知乎 - 去泰国…」归一化后不同 → 重复入书）：
      1. 平台前缀/后缀（「知乎 - 」「- 小红书」）
      2. 所有非字母数字中日韩字符
    """
    v = (t or "").strip()
    v = re.sub(r"^(?:知乎|小红书|微博|Twitter|推特|Notion|微信公众号|B站|油管)\s*[-_|｜]\s*",
               "", v, flags=re.I)
    v = re.sub(r"\s*[-_|｜]\s*(?:知乎|小红书|微博|Twitter|推特|Notion|微信公众号|B站|油管)$",
               "", v, flags=re.I)
    # `\w` 含下划线，而标题尾部的 `_` 只是文件名的分隔残留，要去掉
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", v.lower())


def find_duplicate_title(title: str) -> str:
    """已有 xhtml 里有没有同名文章。

    老快照常常没有 og:url 也没有 var ct，两个判重指纹都算不出来，指纹查重会漏
    （2026-09-17 实测：陈睿那两篇就是这样）。这时候只能靠标题兜底——
    标题归一化后相同，几乎必然是同一篇的另一个快照。
    """
    key = _norm_title(title)
    if not key:
        return ""
    for book in collect_book_dirs():
        xd = book / XHTML_SUBDIR
        if not xd.is_dir():
            continue
        for f in xd.glob("*.xhtml"):
            stem = f.name[:-len("-Sigil.xhtml")] if f.name.endswith("-Sigil.xhtml") else f.stem
            seg = stem.split("_")
            t = "_".join(seg[3:]) if len(seg) > 3 else (seg[2] if len(seg) > 2 else seg[-1])
            if _norm_title(t) == key:
                return "%s/%s" % (book.name, f.name)
    return ""


def register_fingerprints(path: Path, where: str) -> int:
    """把一篇的判重指纹**增量**写进指纹表。

    原 HTML 删掉之后就再也算不出指纹了，所以必须在转换成功的当下登记。
    表是累积的：只加不减，绝不因为某次扫到的文件少就把历史指纹冲掉。
    """
    keys = content_fingerprints(path)
    if not keys:
        return 0
    idx: dict = {}
    if FP_FILE.exists():
        try:
            idx = json.loads(FP_FILE.read_text(encoding="utf-8"))
        except Exception:
            idx = {}
    added = 0
    for k in keys:
        if k not in idx:
            idx[k] = where
            added += 1
    if added:
        try:
            FP_FILE.write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        except OSError as exc:
            log("指纹表写入失败：", exc)
    return added


def _add_one(p: Path, archive_original: bool, dry_run: bool, touched: dict) -> None:
    """处理单个文件：识别号 → 转 XHTML → 归档 → 备份原 HTML。"""
    suffix = p.suffix.lower()
    if suffix in (".html", ".htm"):
        account = detect_account_from_html(p) or "未识别号"
        if not account_allowed(account) and not _cfg_bool("auto_allow_new_accounts", True):
            STATS["pending_accounts"].append("%s（识别为「%s」）" % (p.name, account))
            hold = PENDING_DIR / safe_stem(account) / p.name
            if hold.exists():
                log("  新号「%s」的文件已在待确认里，跳过：%s" % (account, p.name))
                if not dry_run:
                    p.unlink()
                return
            log("  新号「%s」还没确认，先搁置：%s" % (account, p.name))
            if not dry_run:
                hold.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(hold))
            return
        dest_dir = resolve_dir(account)
        sys.path.insert(0, str(SIGI_DIR))
        import sigi_convert as S
        from lxml import html as LH

        # 来源类型决定走哪条转换分支。两种前端产出同一种 XHTML，
        # 但"怎么抽标题/日期/作者"不同，所以要在转换前判一次。
        proc_type = _source_process_type(p)
        src = LH.fromstring(p.read_bytes(), parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
        title = S._article_title_from_source(src, proc_type)
        date_text, base = title.split("_", 1) if "_" in title else ("", title)
        if proc_type == "notion":
            author = S._notion_property(src, "作者").strip()
        else:
            author = S._first_text_by_id(src, "js_author_name") or S._author_from_source(src)
        stem = "_".join(x for x in (date_text, account, author, base) if x)
        target_dir = source_dir(dest_dir)
        converted = False
        dest = target_dir / (S.output_stem_for_title(stem) + "-Sigil.xhtml")
        dup = find_duplicate_title(base)
        if dup:
            # 老快照没有 og:url / var ct，两个判重指纹都算不出来，指纹查重会漏
            # （2026-09-17 实测：陈睿那两篇）。这时候只能靠标题兜底——
            # 标题相同几乎必然是同一篇的另一个快照，收进去书里就有两遍。
            if FORCE_PROCESS:
                log("⚠️ 强制处理：无视标题重复，覆盖重转：", p.name, "→", dup)
            else:
                STATS["trashed"].append("%s（与已有 %s 标题相同，丢废纸篓）" % (p.name, dup))
                log("⚠️ 已存在，丢废纸篓：", p.name, "→", dup)
                if not dry_run:
                    move_to_trash(p)
                return
        if dest.exists():
            if FORCE_PROCESS:
                log("⚠️ 强制处理：覆盖已有同名 xhtml：", dest.name)
                if not dry_run:
                    dest.unlink(missing_ok=True)
            else:
                STATS["trashed"].append("%s（已有同名 xhtml，丢废纸篓）" % dest.name)
                log("⚠️ 已存在，丢废纸篓：", dest.name, "←", p.name)
                if not dry_run:
                    move_to_trash(p)
                return
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            cleaned, n_promo, img_entries = strip_promo_from_html(p)
            n_office = sanitize_office_markup(cleaned)
            if n_office:
                log("  清理 Word 残留标签 %d 处" % n_office)
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
                    S.convert(cleaned, dest, proc_type, False)
                    # 空壳保护：有一种快照转出来只有标题、正文一个字都没有
                    # （2026-09-17 实测：3 篇 33-40MB 的就这样）。
                    # 判据是**正文字数**不是文件字节——短帖（Twitter/微博）
                    # 正文两三百字、文件不到 3KB，但那是正常内容（2026-10-13 修）。
                    shell, chars = is_empty_shell(dest)
                    if shell:
                        raise ValueError("转换结果疑似空壳（正文仅 %d 字）" % chars)
                    STATS["added"].append("%s / %s" % (dest_dir.name, dest.name))
                    converted = True
            except Exception:
                # 失败就别留下半成品：留着会被下一次判成"已有同名"永远跳过
                dest.unlink(missing_ok=True)
                raise
            finally:
                if cleaned != p:
                    cleaned.unlink(missing_ok=True)
        # 只有真出了新的 XHTML 才动原件。转换被「已存在」挡下说明这是重复件。
        if not dry_run and converted:
            # 原件要删了，先把判重指纹和 biz 记下来：删完就再也算不出这些，
            # 以后同一篇重放会被当成新文章收第二遍。
            register_fingerprints(p, "%s/%s" % (dest_dir.name, dest.name))
            register_biz(p, account)
            if archive_original and _cfg_bool("keep_raw_html", False):
                raw_dir = dest_dir / RAW_SUBDIR
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_dest = raw_dir / p.name
                if raw_dest.exists():
                    log("原 HTML 已备份过，删除收件箱副本：", p.name)
                    p.unlink()
                else:
                    shutil.move(str(p), str(raw_dest))
                    log("原 HTML 备份 → %s/%s/" % (dest_dir.name, RAW_SUBDIR))
            else:
                # 陈少 2026-09-17 定：有 xhtml 了，原 html 就没用了，一律删。
                p.unlink(missing_ok=True)
                log("xhtml 已生成，原 HTML 删除：", p.name)
        elif archive_original and not dry_run:
            p.unlink(missing_ok=True)
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
    """把识别出的号名映射到号目录根下已存在的目录。

    目录名和文章里的号名可能不完全一致（例如目录「猫刀笔」/ 号名「猫笔刀」），
    直接新建目录会把同一号拆成两本，所以先做同义匹配再考虑新建。
    """
    clean = safe_stem(account)
    direct = _books_root() / clean        # 2026-10-01 修：新号必须落在号目录根（定稿库），不能建到成品线根下
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


def _books_root() -> Path:
    """号目录所在根。

    2026-09-26 学习工厂迁移：号目录搬进 `02、定稿库/01、微信公众号/`；
    未迁移时仍在 ROOT 根下（旧约定）。用同一条**逐线迁移标记**判断，避免空窗期扫不到号。
    """
    new = ROOT / "02、定稿库" / "01、微信公众号"
    if new.is_dir() and _MIGRATED.is_file():
        return new
    return ROOT


# 平台目录名（与 platforms.PLATFORMS 一致）。判重/台账/体检要跨平台看，
# 否则知乎、小红书里已有的文章会被当成新篇重复收（2026-10-13 踩过）。
_PLATFORM_DIRS = ("01、微信公众号", "02、知乎", "03、小红书", "04、微博",
                  "05、Twitter", "06、B站", "07、油管", "08、出版图书")


def collect_book_dirs():
    """所有平台下的作者目录（每个作者目录 = 一本书）。

    2026-10-13 扩到全平台：原先只扫微信公众号，导致知乎/小红书里的文章
    在判重、台账、体检里都看不见（重复入书的直接原因）。
    """
    lib = ROOT / "02、定稿库"
    roots = [lib / name for name in _PLATFORM_DIRS] if lib.is_dir() else []
    if not roots:
        roots = [_books_root()]                  # 未迁移：退回旧结构
    out: list[Path] = []
    for r in roots:
        if not r.is_dir():
            continue
        out += [d for d in r.iterdir()
                if d.is_dir() and not d.name.startswith((".", "_"))
                and d.name not in SKIP_DIRS and BACKUP_HINT not in d.name]
    return sorted(out)


def source_dir(book_dir: Path) -> Path:
    """该号的 XHTML 目录；老结构（xhtml 直接铺在号目录里）也能兼容。

    兼容回退必须**只对老目录生效**：只有当根下确实散着文稿时才认它是老结构。
    新的/空的号一律进 `xhtml/` 子目录——否则新建号的文章平铺在根目录，
    collect_book_dirs 认不出"这是个号"，台账、体检、后续自动成书会整本漏掉
    （2026-09-17 实测：自动建号后 7 个新号全中招）。
    """
    sub = book_dir / XHTML_SUBDIR
    if sub.is_dir():
        return sub
    if any(book_dir.glob("*.xhtml")):        # 老结构：根目录已经有文稿
        return book_dir
    return sub                                # 新号 / 空目录：用标准结构


COVER_FONTS = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)

# 方正屏显臻宋（封面专用，陈少 2026-10-11 定：封面要屏显臻宋）
COVER_FONT_PATH = "/Users/jessper/Library/Fonts/方正屏显臻宋.TTF"

# 按月分册的号（陈少 2026-10-11 定）：每月独立一本 号_YYYYMM.epub，
# 封面用米白底+屏显臻宋+大字号月份；其他号保持单本合订不变。
SPLIT_MONTH_ACCOUNTS = {"格隆汇图解天下", "猫笔刀"}

# 非公众号线（知乎/小红书/微博/Twitter）的分册门槛（陈少 2026-10-13 定）：
#   ≥ 100 篇 → 按月分册；< 100 篇 → **一个作者一本**（号名.epub，不带月份）。
# 公众号是单独规则（看上方的 SPLIT_MONTH_ACCOUNTS），不适用这个门槛。
PLATFORM_MONTH_SPLIT_MIN = 100

# 封面换版开关（陈少 2026-10-13：视觉统一成格隆汇那套）。
# 平时 False —— 封面已生成就复用，方便手工换图；
# 换版/改模板时临时置 True 跑一轮，全库重生成后再置回。
COVER_REGEN = False


def is_non_wechat_platform(book_dir: Path) -> bool:
    """这个作者目录属于非公众号线（知乎/小红书/微博/Twitter 等）。"""
    try:
        parts = book_dir.resolve().parts
    except OSError:
        parts = book_dir.parts
    return any(p in _PLATFORM_DIRS for p in parts) and "01、微信公众号" not in parts


def should_split_by_month(account: str, book_dir: Path) -> bool:
    """这个作者要不要按月分册。

    公众号：看点名清单（SPLIT_MONTH_ACCOUNTS），单独规则。
    其它平台：篇数 ≥ PLATFORM_MONTH_SPLIT_MIN 才按月，否则一个作者一本。
    """
    if is_non_wechat_platform(book_dir):
        n = len(list(source_dir(book_dir).glob("*.xhtml")))
        return n >= PLATFORM_MONTH_SPLIT_MIN
    return account in SPLIT_MONTH_ACCOUNTS


def _cover_font(size: int):
    """封面统一用屏显臻宋（陈少 2026-10-11 定）；找不到退回系统宋体。"""
    from PIL import ImageFont
    if os.path.exists(COVER_FONT_PATH):
        try:
            return ImageFont.truetype(COVER_FONT_PATH, size)
        except Exception:
            pass
    for p in COVER_FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_cover(account: str, platform: str, month: str = "") -> bytes:
    """统一封面模板（陈少 2026-10-13 定，第二版）。

    三段式，米白底 + 屏显臻宋：
        上：作者 / 号名
        中：平台来源（微信公众号 / 知乎 / 小红书 / 微博 / Twitter）
        下：月份（**只写整月**，如「2026年9月」；不是整月就留空）

    **不要「N 篇」，不要「沪上陈少 藏」落款**（陈少 2026-10-13 明确）。
    月份只有按月分册的书才写（目前只有微信公众号有这个情况），
    一个作者一本的（知乎/小红书/Twitter）下面留空。

    为什么作者放上、平台放中：读者先认「这是谁写的」，再认「从哪来的」。
    """
    from PIL import Image, ImageDraw
    import io

    W, H = 1200, 1600
    BG, INK, SUB, LINE = "#f7f5f0", "#1a1a1a", "#6b6357", "#c9c2b5"
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # 量每段：超宽自动缩字号（绝不溢出，Kindle 裁边就废了），并取真实字高
    entries = [(account, 96, INK, 0.84)]
    if platform:
        entries.append((platform, 104, SUB, 0.84))
    if month:
        entries.append((month, 88, SUB, 0.80))

    measured = []
    for text, size, fill, mw in entries:
        f = _cover_font(size)
        while size > 28:
            box = draw.textbbox((0, 0), text, font=f)
            if (box[2] - box[0]) <= W * mw:
                break
            size -= 4
            f = _cover_font(size)
        box = draw.textbbox((0, 0), text, font=f)
        measured.append((text, f, fill, box[3] - box[1], box))

    # 整体垂直居中：总高 = 各段真实字高 + 段间净空，再定起点
    GAP = 110                                   # 段与段之间的净空
    LINE_AT = 55                                # 分隔线距上一段字底（落在净空里）
    n = len(measured)
    total = sum(m[3] for m in measured) + GAP * (n - 1)
    # 视觉重心略高于正中（陈少 2026-10-13）：纯几何居中看起来会偏下，
    # 整体上移 8% 落回视觉舒适区。
    y = int((H - total) / 2) - int(H * 0.04)

    for idx, (text, f, fill, th, box) in enumerate(measured):
        if idx:
            y += GAP
        if idx == 1 and n > 1:
            # 作者 → 第二段之间的分隔线
            draw.line([(W / 2 - 130, y - GAP + LINE_AT),
                       (W / 2 + 130, y - GAP + LINE_AT)], fill=LINE, width=2)
        draw.text(((W - (box[2] - box[0])) / 2 - box[0], y - box[1]),
                  text, font=f, fill=fill)
        y += th

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def platform_label(book_dir: Path) -> str:
    """作者目录 → 封面要印的平台名（陈少 2026-10-13 定）。

    目录名（01、微信公众号 / 02、知乎 / 03、小红书 / 04、微博 / 05、Twitter）
    去掉序号就是平台名；Twitter 保留英文（陈少指定）。认不出返回空。
    """
    try:
        parts = book_dir.resolve().parts
    except OSError:
        parts = book_dir.parts
    for name in parts:
        for d in _PLATFORM_DIRS:
            if name == d:
                label = d.split("、", 1)[-1]
                return label
    return ""


def ensure_cover(book_dir: Path, account: str, month: str = "",
                 force: bool = False) -> Path | None:
    """号文件夹的 cover.jpg。有就复用（方便自己换图）；force=True 无条件重生成。

    month 只有按月分册的书才传（如「2026年9月」），一个作者一本的传空。
    平台名自动从目录推断，不用调用方传。
    """
    cover = book_dir / COVER_NAME
    if cover.exists() and not force:
        return cover
    try:
        data = render_cover(account, platform_label(book_dir), month)
    except ImportError:
        log("  未装 Pillow，跳过封面生成")
        return None
    cover.write_bytes(data)
    log("  生成封面（米白+屏显臻宋）：", cover.name)
    return cover


def make_cover_bytes(account: str, platform: str, month: str) -> bytes | None:
    """分册号封面（内存生成、不落盘）：每月一本，封面带月份。

    每月封面不同，号目录一个 cover.jpg 不够用，所以直接返回 bytes 嵌进当月那本。
    """
    try:
        data = render_cover(account, platform, month)
    except ImportError:
        log("  未装 Pillow，跳过封面生成")
        return None
    log("  生成封面（米白+屏显臻宋）：", account, platform, month)
    return data


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
            # 必须走文件句柄写入：etree.write(路径) 会把含 "%" 的文件名当 URI 再编码一层
            # （"%"→"%25"），写出来的会是另一个新文件——一篇文章就这么悄悄变成两份（踩过）。
            with open(path, "wb") as fh:
                tree.write(fh, encoding="UTF-8", xml_declaration=True)
            log("  规范化 %s：%d 处" % (path.name[:40], changed))
            changed_total += changed
    return changed_total


# 只盯这一个文件夹（陈少 2026-09-16 定，2026-09-16 下午更新；2026-09-26 学习工厂迁移）：
# 新文章存这里，定时任务每小时收一次。这个文件夹以外的任何地方都不碰。
# 目录里再套一层子文件夹也能收到（SCAN_SUBDIRS）。
# 迁移策略（与 ROOT 同思路）：**双路径探测**——新投递箱（学习工厂/02、文稿成品线/01、投递箱）
# 已建且非空 → 用新箱；否则退回旧源料目录。搬完数据后无需改代码，自动切换。
_WATCH_NEW = Path("/Users/jessper/Life/学习工厂/02、文稿成品线/01、投递箱")
_WATCH_LEGACY = Path("/Users/jessper/Life/01、源料_微信公众号下载")
# 迁移标记：P2 搬完数据后创建；删掉它 → 回退旧源料目录（人工回滚开关）
# 逐线标记：文稿成品线迁完才切 WATCH_DIR
_MIGRATED = Path("/Users/jessper/Life/学习工厂/02、文稿成品线/_迁移完成.marker")
WATCH_DIR = _WATCH_NEW if _MIGRATED.is_file() else _WATCH_LEGACY
SCAN_SUBDIRS = 1
EXTRA_SOURCES = [WATCH_DIR]

# 「可安全清理」只提示不动手：收下来的原 HTML 都会备份到 <号>/原始HTML/，
# 所以下载夹里那份已经是副本，陈少什么时候清都行（我们绝不删他的文件）。
FP_FILE = ROOT / "_已归档指纹.json"

# 名单外的新号：文件先搁这儿，不建书、不混进别人的书；确认后自动回到收件箱
PENDING_DIR = ROOT / "_待确认新号"


def already_archived(name: str) -> bool:
    """该 HTML 是不是已经收过了（归档进某号的 原始HTML/，或搁在 _待确认新号/）。

    搁在待确认里的也算「收过了」——不然每小时扫一次会把同一批新号文章
    反复从下载夹搬一遍。
    """
    for book in collect_book_dirs():
        if (book / RAW_SUBDIR / name).exists():
            return True
    if PENDING_DIR.is_dir():
        for hold in PENDING_DIR.iterdir():
            if hold.is_dir() and (hold / name).exists():
                return True
    return False


# ------------------------------------------------- 只盯一个文件夹：认文章 + 认重复
def walk_html(root: Path, depth: int = SCAN_SUBDIRS) -> list[Path]:
    """列出 root 里的 html（含往下 depth 层子目录）；跳过隐藏文件和隐藏目录。"""
    if not root.is_dir():
        return []
    out: list[Path] = []
    stack = [(root, 0)]
    while stack:
        d, lvl = stack.pop()
        for p in sorted(d.iterdir()):
            if p.name.startswith("."):
                continue
            if p.is_dir():
                if lvl < depth:
                    stack.append((p, lvl + 1))
            elif p.suffix.lower() in (".html", ".htm"):
                out.append(p)
    return out


HEAD_BYTES = 512_000          # _head_text 读的是**字节**数，不是字符数


def _head_text(path: Path, limit: int = HEAD_BYTES) -> str:
    """读文件开头一段（照片 base64 都在正文里，头部足够判断特征）。"""
    try:
        with path.open("rb") as fh:
            return fh.read(limit).decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _has_wechat_mark(text: str) -> bool:
    return any(m in text for m in WECHAT_MARKS) or bool(WECHAT_HEAD_RE.search(text))


def _source_process_type(path: Path) -> str:
    """这个文件该走哪条转换分支：wechat（官网直取）还是 notion（Notion 导出）。

    判据是**结构**，不是文件名——文件名可以随便改，结构改不了。
    Notion 导出认「notion-page-content」或「page-body」容器。
    """
    try:
        text = _head_text(path, limit=3_000_000)
        if "notion-page-content" in text or 'class="page-body"' in text or "page-body" in text:
            return "notion"
    except Exception:
        pass
    return "wechat"


def looks_like_wechat(path: Path) -> bool:
    """判断是不是"微信来源"的文章。

    两种前端都算（陈少 2026-10-13 定）：
      1. 微信官网直取的快照 —— js_content / mmbiz.qpic.cn / og:url
      2. Notion 导出的副本（文章被作者删了，只能靠 Notion 备份恢复）
         —— 结构是 Notion 导出，且属性表「来源/类型」= 微信
    两者在后端产出同一种 XHTML，所以入口这里必须都放行。
    """
    if _has_wechat_mark(_head_text(path)):
        return True
    # Notion 导出的判定交给 adapter 注册表，规则只写一份
    # （adapter 里已经判了"是 Notion 结构 + 来源=微信"）。
    try:
        import core.adapters        # noqa: F401  触发注册
        from core.pipeline import pick
        ad = pick(path)
        if ad is not None and ad.name == "notion_wechat":
            return True
    except Exception:
        pass
    # 判断「头部已经等于全文」要按**字节**比。中文 UTF-8 一个字 3 字节，
    # 拿字符数去比字节上限会让中文文件永远走不到下面的全量兜底
    # （2026-09-17 修：6 篇新文章因此被误判「不是微信文章」）。
    try:
        if path.stat().st_size <= HEAD_BYTES:
            return False                  # 文件就这么大，头里没有就是没有
        full = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return _has_wechat_mark(full)


OG_TITLE_RE = re.compile(r'og:title"\s+content="([^"]*)"')
CT_RE = re.compile(r'var\s+ct\s*=\s*"?(\d{9,10})')
# 文章唯一 ID：只在 og:url / SingleFile 头注释里取。正文里还有大量「别人的文章」链接，
# 全局搜第一个 sn= 会认错（踩过），所以这两处之外的 sn 一律不看。
OG_URL_SN_RE = re.compile(r'og:url"\s+content="[^"]*?\bsn=([0-9a-f]{32})')
SF_URL_SN_RE = re.compile(r'url:\s*https?://mp\.weixin\.qq\.com/\S{0,800}?\bsn=([0-9a-f]{32})')
# Notion 导出把原文链接存在属性表里（可能带 &amp; 转义），sn 就在其中
NOTION_SN_RE = re.compile(r"\bsn=([0-9a-f]{32})")


def content_fingerprints(path: Path) -> list[str]:
    """一篇文章的判重键（可能两个）：微信文章 ID 最稳，`标题+发布时刻` 兜底。

    为什么给多个键：老备份里有的没有 og:url，只能算标题键；如果一边用 sn、
    一边用标题，两种键互相对不上，判重就漏。任一键命中就算同一篇。

    踩过两回：13 篇改名副本；同一篇存了两遍（`chksm` 变了、`sn` 没变）。

    微信直取与 Notion 导出都要能算出键——否则 Notion 文章判不了重，
    会被反复当新篇收（2026-10-13 踩过：源料原件清不掉、有重复入书风险）。
    """
    text = _head_text(path)
    keys: list[str] = []
    m = OG_URL_SN_RE.search(text) or SF_URL_SN_RE.search(text[:2000])
    if not m:
        # Notion 导出的原文链接存在属性表里，没有 og:url / SingleFile 头注释
        m = NOTION_SN_RE.search(text)
    if m:
        keys.append("sn:" + m.group(1))
    m = OG_TITLE_RE.search(text)
    title = html.unescape(m.group(1)).strip() if m else ""
    if not title:
        m2 = re.search(r"var\s+msg_title\s*=\s*'([^']*)'", text)
        title = html.unescape(m2.group(1)).strip() if m2 else ""
    if not title:
        # Notion 导出：标题在 <title> 里，带「 | Notion」后缀要去掉
        m3 = re.search(r"<title>(.*?)</title>", text, re.S)
        if m3:
            title = html.unescape(m3.group(1)).strip()
            title = re.sub(r"(?:\s*[_|｜＿]\s*Notion)+\s*$", "", title, flags=re.I).strip()
    if title:
        ct = CT_RE.search(text)
        title = re.sub(r"\s+", "", title)
        keys.append("tc:" + hashlib.sha1(
            ("%s|%s" % (title, ct.group(1) if ct else "")).encode()).hexdigest()[:16])
    return keys                  # 两个键都算不出来就返回空，退回按文件名判


def fingerprint_index(force: bool = False, dry_run: bool = False) -> dict:
    """已归档文章的指纹表：{指纹: "号/文件名"}。

    缓存在 _已归档指纹.json；备份目录有新增（mtime 更新）就自动重建，所以不用手动维护。
    预演（dry_run）只在内存里算，**不落盘**——预演就该不留痕。
    """
    raw_files = [p for b in collect_book_dirs()
                 for p in (b / RAW_SUBDIR).glob("*.htm*")]
    newest = max((p.stat().st_mtime for p in raw_files), default=0.0)
    if not force and FP_FILE.exists() and FP_FILE.stat().st_mtime >= newest:
        try:
            return json.loads(FP_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 缓存坏了就重建
            pass
    idx: dict[str, str] = {}
    # 先把历史指纹读进来再合并，不覆盖。
    # 原始 HTML 会被清掉（陈少 2026-09-17 定：有 xhtml 就不要 html），
    # 那时下面的循环一个文件也扫不到——直接赋值会把整份判重能力清空，
    # 以后同一篇重放就会被当成新篇收第二遍。
    if FP_FILE.exists():
        try:
            cached = json.loads(FP_FILE.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                idx = cached
        except Exception:
            idx = {}
    for p in raw_files:
        for fp in content_fingerprints(p):
            idx.setdefault(fp, "%s/%s" % (p.parent.parent.name, p.name))
    if dry_run:
        return idx
    try:
        FP_FILE.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        log("指纹表更新：%d 条 → %s" % (len(idx), FP_FILE.name))
    except OSError as exc:
        log("指纹表写不进去（不影响本次处理）：", exc)
    return idx


def account_allowed(account: str) -> bool:
    """这个号收不收。已有目录的号照收；名单外的新号先不建书，等陈少点头。"""
    if not account or ALLOW_ALL_ACCOUNTS:
        return True
    clean = safe_stem(account)
    for d in collect_book_dirs():
        if d.name == clean:
            return True
    for d in collect_book_dirs():          # 一字之差 / 字序颠倒（猫笔刀 vs 猫刀笔）
        if sorted(d.name) == sorted(clean):
            return True
        if len(d.name) == len(clean) and sum(a != b for a, b in zip(d.name, clean)) <= 1:
            return True
    return clean in ALLOWED_ACCOUNTS


def harvest_sources(inbox: Path, dry_run: bool = False) -> int:
    """把盯着的那个文件夹里的新微信文章吸进收件箱。

    下载夹里的原文件**不动**（复制，不是搬走）；已经收过的、以及换了名字的重复副本
    都跳过。跳过的东西记进 STATS，最后由处理报告汇总出来——不静默。
    """
    got = 0
    idx = fingerprint_index(dry_run=dry_run)
    seen_this_run: set[str] = set()
    for src in EXTRA_SOURCES:
        files = walk_html(src)
        if not src.is_dir():
            log("盯的文件夹不存在，跳过：", src)
            continue
        if not files:
            log("盯的文件夹里没有 html：%s" % src)
            continue
        log("盯的文件夹 %s 有 %d 个 html" % (src, len(files)))
        for f in files:
            if not looks_like_wechat(f):
                STATS["non_wechat"].append(str(f))
                log("  不是微信文章，跳过：", f.name)
                continue
            # 指纹必须先算、先登记，再判「收过没有」——否则一个文件因为「已收过」
            # 提前 continue，它的指纹就没进本批次集合，同篇的另一份改名副本会漏网。
            # （踩过：AI 写了一篇存两遍，第二遍被当成新文章收进待确认。）
            fps = content_fingerprints(f)
            hit = next((k for k in fps if k in idx or k in seen_this_run), "")
            if hit:
                where = idx.get(hit) or "本批次里另一份"
                STATS["dup_fp"].append("%s（与 %s 同篇）" % (f.name, where))
                STATS["cleanable"].append(str(f))
                log("  重复副本，跳过：", f.name)
                continue
            for k in fps:
                seen_this_run.add(k)
            if already_archived(f.name) or (inbox / f.name).exists():
                STATS["cleanable"].append(str(f))
                log("  已收过，跳过：", f.name)
                continue
            if dry_run:
                # 预演也要把「会收 / 会搁置」说清楚，否则预演没意义
                acc = detect_account_from_html(f) or "未识别号"
                # 预演的判断必须跟正式跑完全一致，否则预演没意义
                # （2026-09-17 踩过：两边各写一份，预演说会搁置、正式跑其实会收）
                if not account_allowed(acc) and not _cfg_bool("auto_allow_new_accounts", True):
                    STATS["pending_accounts"].append("%s（识别为「%s」）" % (f.name, acc))
                    log("  [预演] 新号「%s」没确认 → 正式跑会搁置：%s" % (acc, f.name))
                else:
                    log("  [预演] 会收：%s（→ %s）" % (f.name, acc))
                    STATS["harvested"].append(str(f))
                    got += 1
                continue
            shutil.copy2(f, inbox / f.name)
            log("  吸入：", f.name)
            STATS["harvested"].append(str(f))
            got += 1
    return got


def process_inbox(inbox: Path, dry_run: bool = False) -> dict[str, Path]:
    """扫描收件箱：识别博主 → 转 XHTML 归档 → 原始 HTML 备份 → 重建受影响的 EPUB。

    按博主和日期自动分流到各自文件夹，一本 EPUB 只收自己号的文章。
    默认收件箱会先把 WATCH_DIR（紧盯着的那一个文件夹）里的新文章吸进来。
    dry_run=True 时只报告、不落地任何文件、不打包。
    """
    if not inbox.is_dir():
        log("收件箱不存在，先建一个：", inbox)
        inbox.mkdir(parents=True, exist_ok=True)
        return {}
    if inbox == INBOX_DIR:
        harvest_sources(inbox, dry_run=dry_run)
    files = sorted(p for p in inbox.iterdir()
                   if p.is_file() and p.suffix.lower() in (".html", ".htm", ".xhtml")
                   and not p.name.startswith("."))
    if not files:
        log("收件箱是空的：", inbox)
        return {}
    log("收件箱 %s 有 %d 个文件" % (inbox.name, len(files)))
    touched = add_files(files, archive_original=True, dry_run=dry_run)
    if dry_run:
        log("（预演模式：一份文件都没落地，也没打包）")
        return {}
    if not touched:
        return {}
    for account, directory in touched.items():
        why = publish_blocked(directory)
        if why:
            log("  暂不成书：%s（%s）" % (account, why))
            STATS["held"].append("%s：%s" % (account, why))
            continue
        log("重建：", account)
        build_all(account, directory)
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
    """刷新线根下的 _台账.md——所有书的一览，随时能看清家底。"""
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
    """把这次运行干了什么写成 _处理报告.md，方便事后回看。

    预演另存 _处理报告_预演.md：预演不该抹掉「上次正式执行」的留痕。
    """
    path = ROOT / ("_处理报告_预演.md" if STATS.get("dry_run") else "_处理报告.md")
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["# 处理报告", "", "- 时间：%s" % now,
             "- 模式：%s" % ("**预演（没落地任何文件）**" if STATS.get("dry_run") else "正式执行"),
             "- 从盯的文件夹吸入：%d 篇" % len(STATS["harvested"]),
             "- 新增归档：%d" % len(STATS["added"]),
             "- 跳过重复：%d" % len(STATS["skipped"]),
             "- 失败：%d" % len(STATS["failed"]),
             "- 丢废纸篓：%d（对应 xhtml 已存在，未重复处理）" % len(STATS["trashed"]),
             "- 暂缓成书：%d（篇数没攒够，等够了自动出书）" % len(STATS["held"]),
             "- 删除推广图：%d 张" % STATS["promo"], ""]
    if STATS["pending_accounts"]:
        lines += ["## 新号待确认（%d）" % len(STATS["pending_accounts"]),
                  "这些文章识别出的公众号不在白名单里，已经搁到 `_待确认新号/`，**没有**混进任何一本书。",
                  "要收就 `./epub.sh allow 号名`（会自动把搁置的文件放回收件箱），不要就不用管。", ""]
        lines += ["- %s" % x for x in STATS["pending_accounts"]]
        lines.append("")
    if STATS["dup_fp"]:
        lines += ["## 重复副本（%d，按内容识别，已跳过）" % len(STATS["dup_fp"]),
                  "同一篇文章换了文件名又存了一遍。只按文件名判重会重复入书，所以这里按标题+发布时刻认。", ""]
        lines += ["- %s" % x for x in STATS["dup_fp"]]
        lines.append("")
    if STATS["non_wechat"]:
        lines += ["## 不是微信文章（%d，已跳过）" % len(STATS["non_wechat"]), ""]
        lines += ["- %s" % x for x in STATS["non_wechat"]]
        lines.append("")
    if STATS["trashed"]:
        lines += ["## 丢废纸篓（%d，对应 xhtml 已存在，未重复处理）" % len(STATS["trashed"]),
                  "这些 HTML 的对应 xhtml 已经存在，按「重复 HTML 丢废纸篓」规则不再处理，"
                  "已移到系统废纸篓（Finder 里可右键「放回原处」恢复）。要恢复就找回废纸篓里的原件。", ""]
        lines += ["- %s" % x for x in STATS["trashed"]]
        lines.append("")
    if STATS["cleanable"]:
        lines += ["## 盯的文件夹里，这些已经收过了（%d，可以安全删掉）" % len(STATS["cleanable"]),
                  "原件都已经备份进 `<号>/原始HTML/` 了，删了也不影响重建。我不动你的文件，你自己清。", ""]
        lines += ["- `%s`" % x for x in sorted(set(STATS["cleanable"]))]
        lines.append("")
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
            book = _books_root() / name
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
                tree = LH.fromstring(f.read_bytes(), parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
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
    global MIN_PUBLISH, ALLOW_ALL_ACCOUNTS, FORCE_PROCESS
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
                    help="成书门槛（源 XHTML 篇数），默认 %d；0 = 不设门槛" % MIN_PUBLISH)
    ap.add_argument("--dry-run", action="store_true",
                    help="预演：只报告会收什么、跳过什么，不落地、不打包")
    ap.add_argument("--force", action="store_true",
                    help="强制处理：无视去重，用新 HTML 覆盖重生成对应 xhtml（陈少发「强制处理」时用）")
    ap.add_argument("--allow", nargs="+", help="把新号加进白名单，并把搁置的文件放回收件箱")
    ap.add_argument("--allow-all", action="store_true", help="本次放行所有号（不写白名单）")
    args = ap.parse_args()

    MIN_PUBLISH = max(0, args.min_articles)
    ALLOW_ALL_ACCOUNTS = args.allow_all
    FORCE_PROCESS = args.force
    STATS["dry_run"] = args.dry_run

    if args.allow:
        names = [n.strip() for n in args.allow if n.strip()]
        allowed = _load_allowed() | set(names)
        ALLOW_FILE.write_text(json.dumps({"allow": sorted(allowed)}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        moved = 0
        for n in names:
            hold = PENDING_DIR / safe_stem(n)
            if not hold.is_dir():
                continue
            INBOX_DIR.mkdir(parents=True, exist_ok=True)
            for f in sorted(hold.iterdir()):
                if f.is_file() and not (INBOX_DIR / f.name).exists():
                    shutil.move(str(f), str(INBOX_DIR / f.name))
                    moved += 1
            try:
                hold.rmdir()
            except OSError:
                pass
        log("已放行：%s → %s" % ("、".join(names), ALLOW_FILE.name))
        if moved:
            log("这些号之前搁置的 %d 个文件已放回收件箱，跑一次 inbox 就会收" % moved)
        return 0

    if args.backfill_ads:
        n_files, n_cands = backfill_ads()
        log("补记账完成：%d 篇，新升候选 %d 个。名单见 ./epub.sh ads" % (n_files, n_cands))
        (ROOT / "_推广图黑名单.md").write_text("\n".join(PROMO.report_lines()), encoding="utf-8")
        return 0

    if args.inbox:
        touched = process_inbox(Path(args.inbox).expanduser().resolve(), dry_run=args.dry_run)
        # 规范化源文件：成书时才跑，新号没成书就永远轮不到，
        # 源文件里会一直留着误判的 h2（2026-09-17 实测：8 个新号 12 处）。
        for account, directory in touched.items():
            try:
                n = tidy_sources(directory)
                if n:
                    log("规范化 %s：%d 处" % (account, n))
            except Exception as exc:      # 单号规范化失败不能拖垮整批
                log("规范化失败：", account, type(exc).__name__, exc)
        # 只有真的出了书的才算「重建」，被门槛挡下的进报告里的「暂缓成书」
        built = [a for a, d in touched.items() if not publish_blocked(d)]
        if not args.dry_run:
            # 陈少 2026-09-17 定：html 一律删。收件箱副本已在 _add_one 里删，
            # 这里补删源料文件夹里的原件（走废纸篓，没归档的不动）。
            try:
                import cleanup
                done, kept = cleanup.find_archived_sources()
                if done:
                    n = cleanup.to_trash(done)
                    log("源料原件清理：%d 个进废纸篓（剩 %d 个未归档留着）" % (n, len(kept)))
                    STATS["cleanable"].extend(str(p) for p in done[:n])
            except Exception as exc:      # 清理失败不能让成书流程挂掉
                log("源料原件清理失败：", type(exc).__name__, exc)
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
        # 按名字在所有平台里找（2026-10-13：原先写死公众号目录，
        # 导致 `only 子不语999` 报"目录不存在"，知乎/小红书根本重建不了）。
        dirs = [d for d in collect_book_dirs() if d.name == args.only] \
            or [_books_root() / args.only]
    else:
        dirs = collect_book_dirs()

    if not dirs:
        log("没有找到公众号目录：", _books_root())
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
