#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信文章 HTML（SingleFile / OpenClaw 保存）→ 可导入 Sigil 的单章 XHTML

逻辑复刻自参考工具（processType="wechat" 分支）：
  /Users/jessper/Documents/Codex/2026-09-07/zheli/outputs/V2转Sigil-网页工具.html

要点：
  正文容器 #js_content；标题 #activity-name；日期 #publish_time（YYYY年M月D日）；
  公众号 #js_name；作者 js_author_name(_text)；meta 行 = 原创/作者/公众号/时间/地区。
  清洗：删脚本样式隐藏节点、img 的 data-src 补 src、去掉 on*/target/危险 URL。
  重排：p/section/h*/img → 统一块结构；<br> 按中英文边界合并（CJK 直接连、西文补空格）；
        居中短句或“第N章/1.2”式短标题 → h2.section-title；空行 → div.spacer。
  输出：带内置 CSS 的自包含 XHTML，正文顶格左对齐，且必须通过 XML 检查。

用法：
  python3 wechat_to_sigil_xhtml.py \
      --src "~/Downloads/微信公众号下载" \
      --out "/Users/jessper/Life/EPUB制作/猫刀笔"
"""
import os, re, sys, glob, argparse, base64, urllib.request, urllib.error
from lxml import html as LH
from lxml import etree

XHTML_NS = "http://www.w3.org/1999/xhtml"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REF = "https://mp.weixin.qq.com/"

REMOVE_TAGS = {"button", "canvas", "form", "iframe", "input", "link", "meta", "script",
               "select", "style", "svg", "template", "textarea", "aside", "footer",
               "header", "nav", "noscript"}
BLOCK_TAGS = {"address", "article", "blockquote", "div", "dl", "dt", "dd", "figure",
              "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main",
              "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
              "thead", "tr", "ul"}
SEMANTIC_TAGS = {"a", "abbr", "b", "bdi", "bdo", "cite", "code", "del", "em", "i",
                 "ins", "kbd", "mark", "q", "s", "samp", "small", "strong", "sub",
                 "sup", "time", "u", "var"}
INLINE_WRAPPER_TAGS = SEMANTIC_TAGS | {"span", "font", "center"}
BREAK_BOUNDARY = {"audio", "canvas", "embed", "figure", "hr", "iframe", "img",
                  "object", "table", "video"}
CENTER_MARKER = "data-sigil-centered"
SECTION_TITLE_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百千]+\s*[章节篇回]"
    r"|[0-9]+\s*[/／]\s*\S.{0,50}"
    r"|[0-9]+\s*[.．]\s*\S.{0,50})")
NUMBERED_PROSE_RE = re.compile(r"^\s*\d+\s*[、]")
OBJ_MARKER = "￼"

BOOK_CSS = """
html { margin: 0; padding: 0; }
body { display: block; margin: 0; padding: 0; color: #111; background: #fff; font-family: serif; font-size: 1em; line-height: 1.6; text-align: left; white-space: normal; overflow-wrap: break-word; }
h1 { display: block; margin: .67em 0; font-size: 1.5em; font-weight: bold; line-height: 1.2; text-align: center; text-indent: 0; }
h2.section-title, h3.section-title { display: block; margin: .83em 0; font-size: 1.375em; font-weight: bold; line-height: 1.2; text-align: center; text-indent: 0; break-after: avoid; }
p { display: block; margin: 7px 0 0; line-height: 1.5; text-align: left; text-indent: 0; white-space: normal; overflow-wrap: break-word; word-break: normal; }
p.lead { font-style: italic; }
p.article-meta { display:block; margin:0 0 1em; color:#777; text-align:left; text-indent:0; white-space:normal; }
p.image { margin: 7px 0 0; text-align: center; text-indent: 0; }
div.spacer { display: block; height: 7px; margin: 0; }
blockquote { margin: 7px 2em 0; text-align: left; }
ul, ol { margin: 7px 0 0 2em; }
li { margin: 0; }
pre { white-space: pre-wrap; }
table { border-collapse: collapse; max-width: 100%; }
img { max-width: 100%; height: auto; }
a { color: inherit; }
"""


# ---------------- 基础工具 ----------------
def norm_text(value):
    return re.sub(r"[\t\r\n\f ]+", " ", (value or "").replace("\u00a0", " "))


def text_of(node):
    if node is None:
        return ""
    if isinstance(node, str):
        return norm_text(node).strip()
    return norm_text(node.text_content()).strip()


def style_value(node, prop):
    style = node.get("style") or ""
    m = re.search(r"(?:^|;)\s*%s\s*:\s*([^;]+)" % prop, style, re.I)
    return m.group(1).strip().lower() if m else ""


def is_hidden(node):
    style = node.get("style") or ""
    classes = (node.get("class") or "").lower().split()
    return (re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I)
            is not None or "sf-hidden" in classes or "hidden" in classes
            or node.get("aria-hidden") == "true")


def is_centered(node):
    if node.get(CENTER_MARKER) == "1":
        return True
    if style_value(node, "text-align") == "center":
        return True
    if (node.get("align") or "").lower() == "center":
        return True
    return bool(re.search(r"(?:^|\s)(?:center|text-center)(?:\s|$)", node.get("class") or "", re.I))


def is_block(el):
    return isinstance(el, LH.HtmlElement) and el.tag.lower() in BLOCK_TAGS


def has_block_child(node):
    return any(is_block(c) for c in node)


def has_meaningful(node):
    return bool(text_of(node) or node.find(".//img") is not None)


def safe_url(name, value):
    v = (value or "").strip().lower()
    if v.startswith(("javascript:", "vbscript:", "data:text/html")):
        return False
    if name == "src" and v.startswith("data:") and not v.startswith("data:image/"):
        return False
    return True


def safe_file_stem(value):
    return re.sub(r'[\\/:*?"<>|]+', "_", (value or "Article")).strip() or "Article"


# ---------------- 字符判定（中英边界） ----------------
def is_cjk_char(ch):
    if not ch:
        return False
    c = ord(ch[0])
    return (0x3400 <= c <= 0x4dbf or 0x4e00 <= c <= 0x9fff or 0xf900 <= c <= 0xfaff
            or 0x20000 <= c <= 0x323af or 0x3040 <= c <= 0x30ff or 0x31f0 <= c <= 0x31ff
            or 0xff66 <= c <= 0xff9d or 0x1100 <= c <= 0x11ff or 0x3130 <= c <= 0x318f
            or 0xac00 <= c <= 0xd7ff)


def is_word_char(ch):
    return bool(ch) and not is_cjk_char(ch) and (ch.isalnum() or ch in "'’-")


# ---------------- 清洗 ----------------
def clean_tree(root):
    if is_centered(root):
        root.set(CENTER_MARKER, "1")
    for node in list(root.iter()):
        if not isinstance(node, LH.HtmlElement):
            continue
        tag = node.tag.lower()
        if tag in REMOVE_TAGS or is_hidden(node) or tag == "wbr":
            parent = node.getparent()
            if parent is not None:
                _drop_keep_tail(node)
            continue
        if is_centered(node):
            node.set(CENTER_MARKER, "1")
        if tag == "img" and not node.get("src"):
            lazy = node.get("data-src") or node.get("data-original")
            if lazy:
                node.set("src", lazy)
        for name in list(node.attrib):
            low = name.lower()
            if low == "target" or low.startswith("on"):
                node.attrib.pop(name)
            elif low in ("href", "src") and not safe_url(low, node.get(name)):
                node.attrib.pop(name)
    # 内联包裹里嵌了块级元素 → 拆开包裹
    for node in list(root.iter()):
        if not isinstance(node, LH.HtmlElement):
            continue
        if node.tag.lower() not in INLINE_WRAPPER_TAGS:
            continue
        if all(not is_block(c) for c in node.iter() if c is not node):
            continue
        parent = node.getparent()
        if parent is None:
            continue
        _unwrap(node)
    return root


def _drop_keep_tail(node):
    """删除节点但保留其 tail 文本"""
    parent = node.getparent()
    if parent is None:
        return
    tail = node.tail
    prev = node.getprevious()
    if tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(node)


def _unwrap(node):
    """把 node 的子节点提到父级，删除 node"""
    parent = node.getparent()
    if parent is None:
        return
    idx = list(parent).index(node)
    prev = node.getprevious()
    children = list(node)
    if node.text:
        if prev is not None:
            prev.tail = (prev.tail or "") + node.text
        else:
            parent.text = (parent.text or "") + node.text
    for i, child in enumerate(children):
        parent.insert(idx + i, child)
    tail = node.tail
    if children:
        last = children[-1]
        last.tail = (last.tail or "") + (tail or "")
    elif tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(node)


# ---------------- 规范化 ----------------
def make(el_tag, **attrs):
    el = LH.Element(el_tag)
    for k, v in attrs.items():
        el.set(k, v)
    return el


def make_spacer():
    el = make("div", **{"class": "spacer"})
    el.text = "\u00a0"
    return el


def append_text_content(target, raw):
    value = norm_text(raw)
    if value and value.strip():
        _append_text(target, value)
        return
    if raw and not re.search(r"[\t\r\n\f]", raw) and raw.replace("\u00a0", "").strip() == "" \
            and (len(target) or (target.text or "").strip()):
        _append_text(target, " ")


def _append_text(target, s):
    if len(target):
        last = target[-1]
        last.tail = (last.tail or "") + s
    else:
        target.text = (target.text or "") + s


def append_inline(target, source):
    if isinstance(source, str):
        append_text_content(target, source)
        return
    if not isinstance(source, LH.HtmlElement):
        return
    tag = source.tag.lower()
    if tag == "br":
        target.append(make("br"))
        return
    if tag == "wbr":
        return
    if tag == "img":
        src = source.get("src") or source.get("data-src") or source.get("data-original")
        if src and safe_url("src", src):
            img = make("img")
            img.set("src", src)
            for attr in ("alt", "title", "width", "height"):
                if source.get(attr):
                    img.set(attr, source.get(attr))
            target.append(img)
        return
    out_tag = tag if tag in SEMANTIC_TAGS else ""
    if not out_tag and tag == "span":
        weight = style_value(source, "font-weight")
        fstyle = style_value(source, "font-style")
        deco = style_value(source, "text-decoration")
        if re.search(r"bold|[6-9]00", weight):
            out_tag = "strong"
        elif re.search(r"italic|oblique", fstyle):
            out_tag = "em"
        elif "underline" in deco:
            out_tag = "u"
    if out_tag:
        wrapper = make(out_tag)
        if source.get("lang"):
            wrapper.set("lang", source.get("lang"))
        for child in _iter_inline_children(source):
            append_inline(wrapper, child)
        if not len(wrapper) and not (wrapper.text or ""):
            src_text = source.text_content() or ""
            if not re.search(r"[\t\r\n\f]", src_text) and src_text.replace("\u00a0", "").strip() == "":
                wrapper.text = " "
        target.append(wrapper)
        return
    for child in _iter_inline_children(source):
        append_inline(target, child)


def _iter_inline_children(node):
    """按文档顺序产出文本片段与元素子节点（含 tail 文本）"""
    out = []
    if node.text:
        out.append(node.text)
    for child in node:
        out.append(child)
        if child.tail:
            out.append(child.tail)
    return out


def trim_edges(node):
    if node.text:
        node.text = node.text.lstrip()
        if not node.text:
            node.text = None
    if len(node):
        last = node[-1]
        if last.tail:
            last.tail = last.tail.rstrip()
    elif node.text:
        node.text = node.text.rstrip()


def only_breaks(node):
    found = [False]

    def visit(current):
        if isinstance(current, str):
            return not norm_text(current).strip()
        if not isinstance(current, LH.HtmlElement):
            return True
        tag = current.tag.lower()
        if tag == "br":
            found[0] = True
            return True
        if tag not in INLINE_WRAPPER_TAGS and tag not in ("ruby", "rt", "rp"):
            return False
        if current.text and norm_text(current.text).strip():
            return False
        for child in current:
            if not visit(child):
                return False
            if child.tail and norm_text(child.tail).strip():
                return False
        return True

    if node.text and norm_text(node.text).strip():
        return False
    for child in node:
        if not visit(child):
            return False
        if child.tail and norm_text(child.tail).strip():
            return False
    return found[0]


def _prev_visible_char(node, br):
    sib = br.getprevious()
    while sib is not None:
        c = _last_char(sib)
        if c:
            return c
        sib = sib.getprevious()
    parent = br.getparent()
    if parent is not None and parent.text:
        t = parent.text
        if t.strip():
            return t.strip()[-1]
    return ""


def _next_visible_char(node, br):
    if br.tail and br.tail.strip():
        return br.tail.strip()[0]
    sib = br.getnext()
    while sib is not None:
        c = _first_char(sib)
        if c:
            return c
        if sib.tail and sib.tail.strip():
            return sib.tail.strip()[0]
        sib = sib.getnext()
    return ""


def _last_char(el):
    if isinstance(el, str):
        for ch in reversed(el):
            if not ch.isspace():
                return ch
        return ""
    if el.tag.lower() in BREAK_BOUNDARY:
        return OBJ_MARKER
    if len(el):
        c = _last_char(el[-1])
        if c:
            return c
    if el.tail:
        for ch in reversed(el.tail):
            if not ch.isspace():
                return ch
    if el.text:
        for ch in reversed(el.text):
            if not ch.isspace():
                return ch
    return ""


def _first_char(el):
    if isinstance(el, str):
        for ch in el:
            if not ch.isspace():
                return ch
        return ""
    if el.tag.lower() in BREAK_BOUNDARY:
        return OBJ_MARKER
    if el.text:
        for ch in el.text:
            if not ch.isspace():
                return ch
    for child in el:
        c = _first_char(child)
        if c:
            return c
    return ""


def flatten_breaks(node):
    for br in list(node.iter("br")):
        parent = br.getparent()
        if parent is None:
            continue
        before = _prev_visible_char(node, br)
        after = _next_visible_char(node, br)
        cjk = is_cjk_char(before) and is_cjk_char(after)
        word = is_word_char(before) and is_word_char(after)
        tail = br.tail or ""
        prev = br.getprevious()
        if (cjk or word) and tail:
            tail = tail.lstrip()
        if cjk or word:
            if prev is not None:
                if prev.tail:
                    prev.tail = prev.tail.rstrip()
            elif parent.text:
                parent.text = parent.text.rstrip()
        if word and tail:
            tail = " " + tail
        if tail:
            if prev is not None:
                prev.tail = (prev.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail
        parent.remove(br)
    return node


def prune_empty_inline(node):
    for child in list(node):
        prune_empty_inline(child)
        tag = child.tag.lower()
        if tag in SEMANTIC_TAGS and not text_of(child) and child.find(".//img") is None:
            _unwrap(child)
    return node


def is_heading_text(text):
    return 0 < len(text) <= 60 and bool(SECTION_TITLE_RE.match(text))


def is_numbered_prose(text):
    return bool(NUMBERED_PROSE_RE.match(text or ""))


def is_heading_source(node, text=None):
    text = text_of(node) if text is None else text
    tag = node.tag.lower() if isinstance(node, LH.HtmlElement) else ""
    centered = tag in ("p", "section") and node.get(CENTER_MARKER) and not has_block_child(node)
    if is_numbered_prose(text):
        return False
    # 纯图片块（居中且无文字）不是小标题，不能被判成 h2，否则图片会塞进标题里
    if not text and isinstance(node, LH.HtmlElement) and node.find(".//img") is not None:
        return False
    return tag in ("h1", "h2", "h3", "h4", "h5", "h6") or is_heading_text(text) or bool(centered)


def finish_paragraph(paragraph, source):
    if only_breaks(paragraph):
        return [make_spacer()]
    flatten_breaks(paragraph)
    prune_empty_inline(paragraph)
    trim_edges(paragraph)
    if not has_meaningful(paragraph):
        return []
    if is_heading_source(source, text_of(paragraph)):
        heading = make("h2", **{"class": "section-title"})
        for child in _iter_inline_children(paragraph):
            append_inline(heading, child)
        trim_edges(heading)
        return [heading]
    paragraph.set("class", "body-text")
    return [paragraph]


def _normalise_children(node):
    """公共流程：块级递归，内联累积成段落"""
    output = []
    paragraph = make("p")

    def flush():
        nonlocal paragraph
        output.extend(finish_paragraph(paragraph, node))
        paragraph = make("p")

    items = _iter_inline_children(node)
    for item in items:
        if isinstance(item, LH.HtmlElement) and is_block(item):
            flush()
            output.extend(normalise_block(item))
        else:
            append_inline(paragraph, item)
    flush()
    return output


def normalise_block(node):
    tag = node.tag.lower()
    if tag == "p":
        return _normalise_children(node)
    if tag == "section":
        if only_breaks(node):
            return [make_spacer()]
        text = text_of(node)
        has_image = node.find(".//img") is not None
        if (not has_block_child(node) and not is_numbered_prose(text) and not has_image
                and (is_heading_text(text) or is_centered(node))):
            heading = make("h2", **{"class": "section-title"})
            for child in _iter_inline_children(node):
                append_inline(heading, child)
            trim_edges(heading)
            return [heading] if has_meaningful(heading) else []
        return _normalise_children(node)
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        if is_numbered_prose(text_of(node)):
            paragraph = make("p")
            for child in _iter_inline_children(node):
                append_inline(paragraph, child)
            return finish_paragraph(paragraph, node)
        heading = make("h1" if tag == "h1" else "h2", **{"class": "section-title"})
        for child in _iter_inline_children(node):
            append_inline(heading, child)
        trim_edges(heading)
        return [heading] if has_meaningful(heading) else []
    if tag == "img":
        paragraph = make("p", **{"class": "image"})
        append_inline(paragraph, node)
        return [paragraph]
    if tag in ("br", "wbr"):
        return [make_spacer()]
    if tag == "hr":
        return [make("hr")]
    if has_block_child(node):
        return _normalise_children(node)
    if not has_meaningful(node):
        return []
    paragraph = make("p")
    for child in _iter_inline_children(node):
        append_inline(paragraph, child)
    return finish_paragraph(paragraph, node)


def normalise_article(article, title, metadata):
    cleaned = clean_tree(article)
    blocks = _normalise_children(cleaned)
    compact = []
    for block in blocks:
        if block.get("class") == "spacer" and compact and compact[-1].get("class") == "spacer":
            continue
        compact.append(block)
    for block in compact:
        if block.tag.lower() != "pre":
            flatten_breaks(block)
            prune_empty_inline(block)
    has_visible_title = any(block.tag.lower() in ("h1", "h2") and text_of(block) == title
                            for block in compact)
    if title and not has_visible_title:
        h1 = make("h1")
        h1.text = title
        compact.insert(0, h1)
    if metadata:
        meta = make("p", **{"class": "article-meta"})
        meta.text = " · ".join(metadata)
        pos = 1 if (compact and compact[0].tag.lower() == "h1") else 0
        compact.insert(pos, meta)
    idx = 0
    for node in compact:
        if node.tag.lower() == "h2":
            idx += 1
            node.set("id", "section-%d" % idx)
        node.attrib.pop(CENTER_MARKER, None)
        for marked in list(node.iter()):
            marked.attrib.pop(CENTER_MARKER, None)
    for node in compact:
        if node.tag.lower() == "p" and node.get("class") != "article-meta" and node.find(".//em") is None and node.find(".//i") is None:
            continue
        if node.tag.lower() == "p" and node.get("class") != "article-meta":
            node.set("class", (node.get("class") or "") + " lead")
            break
    return compact


# ---------------- SingleFile / OpenClaw 背景图还原 ----------------
# SingleFile 把图片抽成 CSS 变量（--sf-img-N: url("data:image/...")），正文元素
# 再用 background-image: var(--sf-img-N) 引用，导致正文里没有 <img> 标签。
# 这里把变量映射解析出来，并把正文内的背景图还原成真正的 <img>。
SF_VAR_RE = re.compile(r'(--sf-img-\d+)\s*:\s*url\(\s*["\']?(data:image/[^"\')\s]+)["\']?\s*\)')
SF_USE_RE = re.compile(r'var\(\s*(--sf-img-\d+)\s*\)')


def extract_sf_image_vars(doc):
    mapping = {}
    for style in doc.iter("style"):
        css = style.text_content() or ""
        for name, uri in SF_VAR_RE.findall(css):
            mapping[name] = uri
    return mapping


def restore_singlefile_images(article, mapping):
    """仅在正文容器内把 background-image: var(--sf-img-N) 还原为 <img>"""
    if not mapping:
        return 0
    restored = 0
    for el in list(article.iter()):
        if not isinstance(el, LH.HtmlElement):
            continue
        style = el.get("style") or ""
        m = SF_USE_RE.search(style)
        if not m:
            continue
        uri = mapping.get(m.group(1))
        if not uri:
            continue
        cleaned = re.sub(r'background(?:-image)?\s*:\s*[^;]*var\([^)]*\)[^;]*;?', '', style, flags=re.I)
        el.set("style", cleaned)
        if el.tag.lower() == "img":
            if not el.get("src"):
                el.set("src", uri)
                restored += 1
        else:
            img = LH.Element("img")
            img.set("src", uri)
            el.append(img)
            restored += 1
    return restored


def http_get(url, binary=False, timeout=25):
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "ignore")


def _image_ext(content_type, url):
    m = re.search(r"image/(\w+)", content_type or "")
    if m:
        e = m.group(1).lower()
        return {"jpeg": "jpeg", "jpg": "jpeg", "png": "png", "gif": "gif",
                "webp": "webp", "bmp": "bmp"}.get(e, "jpeg")
    m = re.search(r"wx_fmt=(\w+)", url or "")
    if m:
        return m.group(1).lower()
    return "jpeg"


def inline_lazy_images(article, timeout=25):
    """
    微信懒加载图片：src 常是 1x1 的 SVG 占位符，真图地址在 data-src（远程图床）。
    检测占位图并下载真图内联为 base64，保证导出的 XHTML 自包含、离线可读。
    """
    downloaded = 0
    for im in list(article.iter("img")):
        src = im.get("src") or ""
        lazy = im.get("data-src") or im.get("data-original") or ""
        placeholder = src.startswith("data:image/svg") or "placeholder" in (im.get("class") or "").lower()
        if not (placeholder and lazy.startswith(("http://", "https://", "//"))):
            continue
        try:
            req = urllib.request.Request(lazy, headers={"User-Agent": UA, "Referer": REF})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "")
            if not data or len(data) < 64:
                continue
            ext = _image_ext(ctype, lazy)
            im.set("src", "data:image/%s;base64,%s" % (ext, base64.b64encode(data).decode("ascii")))
            downloaded += 1
        except Exception as e:
            print("    图下载失败:", lazy[:60], e)
    return downloaded


# ---------------- 字段提取 ----------------
def _by_id(doc, _id):
    try:
        return doc.get_element_by_id(_id)
    except Exception:
        return None


def extract_article_meta(doc):
    values = []
    for _id in ("copyright_logo", "js_author_name_text", "js_author_name", "js_name",
                "publish_time", "js_ip_wording"):
        v = text_of(_by_id(doc, _id))
        if v and v not in values:
            values.append(v)
    if not values:
        for m in doc.iter("meta"):
            if (m.get("name") or "").lower() == "author" and (m.get("content") or "").strip():
                values.append(m.get("content").strip())
                break
    return values


def extract_publish_date(doc):
    value = text_of(_by_id(doc, "publish_time"))
    m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", value)
    return m.group(1) if m else ""


def extract_author(doc):
    for _id in ("js_author_name_text", "js_author_name"):
        v = text_of(_by_id(doc, _id))
        if v:
            return v
    for m in doc.iter("meta"):
        if (m.get("name") or "").lower() == "author" and (m.get("content") or "").strip():
            return m.get("content").strip()
    return ""


def extract_account(doc):
    return text_of(_by_id(doc, "js_name"))


def select_article(doc):
    candidate = _by_id(doc, "js_content")
    if candidate is not None and (len(candidate) or text_of(candidate)):
        return candidate
    if candidate is not None and candidate.getparent() is not None:
        repaired = LH.Element("div")
        sib = candidate.getnext()
        while sib is not None:
            repaired.append(sib)
            sib = candidate.getnext()
        if len(repaired):
            return repaired
    for sel in ("//article", "//main", "//*[@role='main']"):
        found = doc.xpath(sel)
        if found:
            return found[0]
    body = doc.find("body")
    return body if body is not None else doc


# ---------------- XHTML 组装 ----------------
def to_xml(el, parent):
    """把 lxml.html 元素树转成带 XHTML 命名空间的 XML 树"""
    if isinstance(el, str):
        if parent is not None:
            if len(parent):
                parent[-1].tail = (parent[-1].tail or "") + el
            else:
                parent.text = (parent.text or "") + el
        return None
    node = etree.SubElement(parent, "{%s}%s" % (XHTML_NS, el.tag.lower()))
    for k, v in el.attrib.items():
        node.set(k, v)
    # 不要在这里预设 node.text：_iter_inline_children 会把 el.text 作为首段文本产出，
    # 预设再加一次会导致文本重复（标题/meta 出现两遍）。
    for child in _iter_inline_children(el):
        to_xml(child, node)
    return node


def image_report(body):
    # XML 树里 tag 带命名空间，不能用 body.iter("img") 直接匹配
    imgs = [e for e in body.iter() if isinstance(e.tag, str) and e.tag.split("}")[-1] == "img"]
    rep = {"images": len(imgs), "embedded": 0, "external": 0, "missing": 0}
    for im in imgs:
        src = (im.get("src") or "").strip().lower()
        if not src:
            rep["missing"] += 1
        elif src.startswith("data:image/"):
            rep["embedded"] += 1
        elif src.startswith(("http://", "https://")):
            rep["external"] += 1
    return rep


def build_xhtml(blocks, title):
    root = etree.Element("{%s}html" % XHTML_NS, nsmap={None: XHTML_NS})
    head = etree.SubElement(root, "{%s}head" % XHTML_NS)
    meta = etree.SubElement(head, "{%s}meta" % XHTML_NS)
    meta.set("http-equiv", "Content-Type")
    meta.set("content", "application/xhtml+xml; charset=UTF-8")
    t = etree.SubElement(head, "{%s}title" % XHTML_NS)
    t.text = title
    style = etree.SubElement(head, "{%s}style" % XHTML_NS)
    style.set("type", "text/css")
    style.text = BOOK_CSS
    body = etree.SubElement(root, "{%s}body" % XHTML_NS)
    for block in blocks:
        to_xml(block, body)
    return root, body


def validate(body):
    """参考工具的硬性契约：正文不得残留 br 与布局属性"""
    for br in body.iter("{%s}br" % XHTML_NS):
        ancestors = [a.tag for a in br.iterancestors()]
        if "{%s}pre" % XHTML_NS not in ancestors:
            return "正文仍包含硬换行（br），未生成文件"
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.split("}")[-1]
        style = el.get("style") or ""
        color_only = bool(re.match(r"^\s*color\s*:\s*[^;]+\s*$", style, re.I))
        if el.get("style") and not color_only:
            return "正文仍包含网页布局属性（style），未生成文件"
        if (el.get("width") or el.get("height")) and tag != "img":
            return "正文仍包含网页布局属性（width/height），未生成文件"
    return None


def convert_file(path, out_dir):
    raw = open(path, encoding="utf-8", errors="ignore").read()
    doc = LH.document_fromstring(raw)
    article = select_article(doc)
    if article is None:
        raise RuntimeError("未找到可转换的正文区域")
    base_title = text_of(_by_id(doc, "activity-name")) or norm_text(doc.findtext(".//title") or "Article").strip() or "Article"
    publish_date = extract_publish_date(doc)
    title = "%s_%s" % (publish_date, base_title) if publish_date else base_title
    metadata = extract_article_meta(doc)
    account = extract_account(doc)
    author = extract_author(doc)
    # SingleFile 背景图 → <img>（必须在 clean_tree 删掉 <style> 之前做）
    sf_map = extract_sf_image_vars(doc)
    restored = restore_singlefile_images(article, sf_map)
    downloaded = inline_lazy_images(article)
    blocks = normalise_article(article, title, metadata)
    root, body = build_xhtml(blocks, title)
    err = validate(body)
    if err:
        raise RuntimeError(err)
    serialized = etree.tostring(root, encoding="unicode")
    output = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n' + serialized
    # XML 良构自检
    etree.fromstring(serialized.encode("utf-8"))
    stem = safe_file_stem("_".join([p for p in (publish_date, account, author, base_title) if p]))
    name = "%s-Sigil.xhtml" % stem
    out_path = os.path.join(out_dir, name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)
    return {"path": out_path, "title": title, "date": publish_date, "account": account,
            "author": author, "report": image_report(body), "restored": restored,
            "downloaded": downloaded,
            "paragraphs": sum(1 for b in blocks if b.tag.lower() == "p" and b.get("class") != "article-meta")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.expanduser("~/Downloads/微信公众号下载"))
    ap.add_argument("--out", default="/Users/jessper/Life/EPUB制作/猫刀笔")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(os.path.expanduser(args.src), "*.html")))
    if not files:
        print("源目录没有 HTML：", args.src)
        return
    ok = fail = 0
    for path in files:
        name = os.path.basename(path)
        try:
            r = convert_file(path, args.out)
            ok += 1
            rep = r["report"]
            print("✓ %s\n    → %s\n    %s · %d段 · 图%d(内嵌%d/外链%d/缺%d) · 懒加载补图%d"
                  % (name[:44], os.path.basename(r["path"]), r["title"][:40], r["paragraphs"],
                     rep["images"], rep["embedded"], rep["external"], rep["missing"],
                     r["downloaded"]))
        except Exception as e:
            fail += 1
            print("✗ %s\n    失败：%s" % (name[:44], e))
    print("\n完成：成功 %d，失败 %d → %s" % (ok, fail, args.out))


if __name__ == "__main__":
    main()
