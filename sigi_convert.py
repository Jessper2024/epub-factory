#!/usr/bin/env python3
"""Convert a SingleFile HTML snapshot into XHTML ready for Sigil."""

from __future__ import annotations

import argparse
import base64
import copy
import datetime
import mimetypes
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from lxml import etree, html


DOCTYPE = '<!DOCTYPE html>'
ALLOWED_ATTRIBUTES = {
    "alt",
    "height",
    "href",
    "lang",
    "rel",
    "src",
    "target",
    "title",
    "width",
}
REMOVE_TAGS = {
    "button",
    "canvas",
    "form",
    "iframe",
    "input",
    "link",
    "meta",
    "script",
    "select",
    "style",
    "svg",
    "template",
    "textarea",
    "aside",
    "footer",
    "header",
    "nav",
    "noscript",
}

# Tags which carry document semantics are retained.  Layout-only wrappers are
# flattened below so that a browser's visual layout does not become document
# structure in the generated XHTML.
SEMANTIC_INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "bdi",
    "bdo",
    "cite",
    "code",
    "del",
    "em",
    "i",
    "ins",
    "kbd",
    "mark",
    "q",
    "s",
    "samp",
    "small",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
}
INLINE_WRAPPER_TAGS = SEMANTIC_INLINE_TAGS | {"span", "font", "center"}
INTERNAL_CENTER_ATTR = "data-sigil-centered"
INTERNAL_COLOR_ATTR = "data-sigil-color"
BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "div",
    "dl",
    "dt",
    "dd",
    "figure",
    "figcaption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
# Keep section detection deliberately conservative.  A frequent WeChat
# article pattern is ``1、正文……``; that is an ordinary numbered paragraph,
# not a chapter heading, so the Chinese enumeration comma is intentionally not
# accepted here.  Explicit chapter labels and short ``1. Title``/``1/Title``
# forms remain supported.
SECTION_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9一二三四五六七八九十百千]+\s*[章节篇回]"
    r"|[0-9]+\s*/\s*\S.{0,59}"
    r"|[0-9]+\s*[.．]\s*\S.{0,59}"
    r")"
)
SECTION_TITLE_MAX_LEN = 60
PLAIN_NUMBERED_RE = re.compile(r"^\s*\d+\s*[、]")
BOOK_CSS = """\
html { margin: 0; padding: 0; }
body {
  display: block;
  margin: 0;
  padding: 0;
  color: #111;
  background: #fff;
  font-family: serif;
  font-size: 1em;
  line-height: 1.6;
  text-align: left;
}
h1 {
  display: block;
  margin: .67em 0;
  font-size: 1.5em;
  font-weight: bold;
  line-height: 1.2;
  text-align: center;
  text-indent: 0;
}
h2.section-title, h3.section-title {
  display: block;
  margin: .83em 0;
  font-size: 1.375em;
  font-weight: 700;
  line-height: 1.2;
  text-align: center;
  text-indent: 0;
  break-after: avoid;
}
p {
  display: block;
  margin: 7px 0 0;
  line-height: 1.5;
  text-align: left;
  text-indent: 0;
  white-space: normal;
  word-break: normal;
  overflow-wrap: break-word;
}
div.spacer {
  display: block;
  height: 7px;
  margin: 0;
}
p.lead {
  font-style: italic;
}
.article-meta {
  display: block;
  margin: 0 0 1em;
  color: #777;
  text-align: left;
  text-indent: 0;
  white-space: normal;
}
p.image {
  margin: 7px 0 0;
  text-align: center;
  text-indent: 0;
}
blockquote {
  margin: 7px 2em 0;
  text-align: left;
}
ul, ol { margin: 7px 0 0 2em; }
li { margin: 0; }
pre { white-space: pre-wrap; }
table { border-collapse: collapse; max-width: 100%; }
img { max-width: 100%; height: auto; }
a { color: inherit; }
"""


def safe_url(name: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered.startswith(("javascript:", "vbscript:", "data:text/html")):
        return False
    if name == "src" and lowered.startswith("data:") and not lowered.startswith("data:image/"):
        return False
    return True


def _is_placeholder_image(value: str, node: etree._Element = None) -> bool:
    """Detect SingleFile/微信 lazy-load placeholder images.

    微信懒加载图片的 src 常是 1×1 的 SVG 占位符，真图地址放在 data-src。
    占位符本身是合法的 data: URI，若被当成有效图片源，真图就永远不会被下载，
    导出的 XHTML 里只会剩下空白小方块。
    """

    lowered = (value or "").strip().lower()
    if lowered.startswith("data:image/svg"):
        return True
    # 已经是真实图片数据（内联 base64 或 http 地址）就不再是占位符。
    # 注意：微信懒加载图的 class 常带 "placeholder" 且终生不变，
    # 若只看 class，内嵌后的 base64 真图会被误判成占位符，
    # 规范化阶段又会把 src 换回 data-src 外链。
    if lowered.startswith(("data:image/", "http://", "https://")):
        return False
    if node is not None and "placeholder" in (node.get("class") or "").lower():
        return True
    return False


def image_source(node: etree._Element) -> str:
    """Choose the first usable image URL from SingleFile lazy-load fields."""

    def normalise(value: str) -> str:
        value = value.strip()
        try:
            if re.match(r"^/?image/", value, re.I):
                return unquote(re.sub(r"^/?image/", "", value, flags=re.I).split("?", 1)[0])
            if re.match(r"^/_next/image\?", value, re.I):
                encoded = parse_qs(urlsplit(value).query).get("url", [""])[0]
                if encoded:
                    return unquote(encoded)
        except ValueError:
            pass
        return value

    # 占位图不是有效图片源：跳过它，继续在懒加载字段里找真实地址。
    src_is_placeholder = _is_placeholder_image(node.get("src") or "", node)

    for name in ("src", "data-src", "data-original", "data-original-src", "data-lazy-src", "data-url"):
        value = normalise(node.get(name) or "")
        if not value or value.lower() == "about:blank" or not safe_url("src", value):
            continue
        if name == "src" and src_is_placeholder:
            continue
        return value
    for candidate in (node.get("srcset") or node.get("data-srcset") or "").split(","):
        value = normalise(candidate.strip().split(None, 1)[0]) if candidate.strip() else ""
        if value and safe_url("src", value):
            return value
    return ""


def _notion_public_origin(source: etree._Element) -> str:
    """Find the public Notion site origin shown in a SingleFile page chrome."""

    match = re.search(r"\b([a-z0-9-]+\.notion\.site)\b", " ".join(source.itertext()), re.I)
    return f"https://{match.group(1)}" if match else ""


def _singlefile_page_url(source: etree._Element) -> str:
    """Recover the original page URL from a SingleFile snapshot.

    SingleFile writes `url: <original>` in a leading HTML comment. 微信图床
    (mmbiz.qpic.cn) 校验 Referer，必须以文章页为来源，否则图片会下载失败并
    退化成外链，因此没有 <base href> 时用它作为 Referer/相对地址基准。
    """

    for node in source.xpath("//comment()"):
        match = re.search(r"\burl:\s*(\S+)", str(node))
        if match:
            return match.group(1).strip()
    for expr in ("//meta[@property='og:url']/@content", "//link[@rel='canonical']/@href"):
        values = source.xpath(expr)
        if values:
            return str(values[0]).strip()
    return ""


def _embed_external_images(
    root: etree._Element, base_href: str = "", public_base_href: str = ""
) -> dict[str, int]:
    """Download reachable image URLs and replace them with data URIs.

    This runs in the local converter, where browser CORS restrictions do not
    apply. Private Notion attachments are requested from the original page
    host; no third-party image proxy is used.
    """

    embedded = failed = attempted = 0
    for image in root.xpath('.//*[local-name()="img"]'):
        raw = (image.get("src") or "").strip()
        source = image_source(image)
        candidates: list[str] = []
        if raw.startswith(("/", "./")) and base_href:
            candidates.append(urljoin(base_href, raw))
        if raw.startswith(("/", "./")) and public_base_href:
            public_url = urlsplit(urljoin(public_base_href, raw))
            query = parse_qs(public_url.query, keep_blank_values=True)
            query.pop("userId", None)
            candidates.append(public_url._replace(query=urlencode(query, doseq=True)).geturl())
        if source.startswith(("http://", "https://")):
            candidates.append(source)
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            continue
        attempted += 1
        for candidate in candidates:
            try:
                request = Request(candidate, headers={"User-Agent": "Mozilla/5.0", "Referer": base_href or candidate})
                with urlopen(request, timeout=20) as response:
                    payload = response.read()
                    content_type = (response.headers.get_content_type() or "").lower()
                if not content_type.startswith("image/"):
                    guessed = mimetypes.guess_type(urlsplit(candidate).path)[0] or ""
                    content_type = guessed.lower()
                if not content_type.startswith("image/") or not payload:
                    continue
                image.set("src", f"data:{content_type};base64,{base64.b64encode(payload).decode('ascii')}")
                embedded += 1
                break
            except (HTTPError, URLError, TimeoutError, OSError):
                continue
        else:
            failed += 1
        if raw.startswith(("/", "./")) and not (image.get("src") or "").startswith("data:image/"):
            fallback = public_base_href or base_href
            if fallback:
                resolved = urlsplit(urljoin(fallback, raw))
                if fallback == public_base_href:
                    query = parse_qs(resolved.query, keep_blank_values=True)
                    query.pop("userId", None)
                    resolved = resolved._replace(query=urlencode(query, doseq=True))
                image.set("src", resolved.geturl())
    return {"attempted": attempted, "embedded": embedded, "failed": failed}


def output_stem_for_title(title: str) -> str:
    """Return a filesystem-safe stem while retaining readable Unicode text.

    ASCII "%" MUST be swapped for the full-width "％": libxml2 treats a path
    with "%" as a URI when writing (re-encoding "%" into "%25") but parses
    by the literal name, so each parse→write cycle mints a new file with one
    more "%25" layer — one article silently becomes two (verified 2026-09-16).
    """

    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", title).replace("%", "％").strip(" .")
    return cleaned or "Article"


def default_output(input_path: Path, title: str | None = None) -> Path:
    stem = output_stem_for_title(title) if title else input_path.stem
    candidate = input_path.with_name(f"{stem}-Sigil.xhtml")
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = input_path.with_name(f"{stem}-Sigil-{index}.xhtml")
        if not candidate.exists():
            return candidate
        index += 1


def _tag(node: etree._Element) -> str:
    """Return a lower-case local tag name for HTML or XHTML nodes."""

    if not isinstance(node.tag, str):
        return ""
    return etree.QName(node).localname.lower()


def _normalise_text(value: str | None) -> str:
    if not value:
        return ""
    # SingleFile snapshots contain indentation/newlines between almost every
    # span.  They are layout whitespace, not author-intended line breaks.
    return re.sub(r"[\t\r\n\f ]+", " ", value.replace("\xa0", " "))


def _text_value(node: etree._Element) -> str:
    return _normalise_text("".join(node.itertext())).strip()


def _first_text_by_id(source: etree._Element, element_id: str) -> str:
    nodes = source.xpath(f'//*[@id="{element_id}"]')
    return _text_value(nodes[0]) if nodes else ""


def _extract_article_meta(source: etree._Element) -> list[str]:
    """Collect visible WeChat/SingleFile header metadata in display order."""

    values: list[str] = []
    # ``js_author_name_text`` is the visible account name in newer WeChat
    # snapshots; ``js_author_name`` is often hidden but remains the canonical
    # author value.  Keep both when they differ (the header commonly displays
    # e.g. ``moomoocat`` and ``猫笔刀``), while avoiding duplicate spans.
    for element_id in (
        "copyright_logo",
        "js_author_name_text",
        "js_author_name",
        "js_name",
        "publish_time",
        "js_ip_wording",
    ):
        value = _first_text_by_id(source, element_id)
        if value and value not in values:
            values.append(value)
    if not values:
        author_nodes = source.xpath('//meta[translate(@name, "AUTHOR", "author")="author"]/@content')
        if author_nodes and author_nodes[0].strip():
            values.append(author_nodes[0].strip())
    return values


def _date_from_publish_time(value: str) -> str:
    """Extract the calendar date used in the exported article title."""

    match = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", value or "")
    return match.group(1) if match else ""


_TIMESTAMP_RE = re.compile(
    r'(?:var\s+ct\s*=\s*["\']?|"publish_time"\s*:\s*["\']?'
    r'|oriCreateTime\s*=\s*["\']?|create_time\s*=\s*["\']?)(1[6-9]\d{8})')


def _date_from_timestamp_source(source) -> str:
    """页面里读不到「YYYY年M月D日」时的兜底：从 JS 时间戳推日期（UTC+8）。

    2026-09-17 补：一批 SingleFile 存档的 ``#publish_time`` 是**空节点**（保存时页面
    没渲染完），但 ``var ct`` / ``publish_time`` 时间戳还在，只是位置靠后——既不在
    DOM 里、也不在文件头 512KB 内。不兜底就会被当成「未标注日期」单独成一章。
    时间戳只在日期缺失时才找，正常文章不会走到这里。
    """
    text = ""
    try:
        text = etree.tostring(source, encoding="unicode")
    except Exception:  # noqa: BLE001 - 兜底失败就当没日期，不能影响主流程
        return ""
    match = _TIMESTAMP_RE.search(text or "")
    if not match:
        return ""
    try:
        stamp = int(match.group(1))
        moment = (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=stamp)
                  + datetime.timedelta(hours=8))
    except (ValueError, OverflowError):
        return ""
    return "%d年%d月%d日" % (moment.year, moment.month, moment.day)


def _date_text_from_source(source) -> str:
    """文章日期：先读可见的发布时间，读不到再退回 JS 时间戳。"""

    return (_date_from_publish_time(_first_text_by_id(source, "publish_time"))
            or _date_from_timestamp_source(source))


NOTION_TITLE_SUFFIX_RE = re.compile(r"(?:\s*[_|｜＿]\s*Notion)+\s*$", re.I)


def _clean_title(value: str) -> str:
    """Remove trailing Notion branding, without changing mentions in a title."""

    return NOTION_TITLE_SUFFIX_RE.sub("", _normalise_text(value).strip()).strip()


def _raw_title_from_source(source: etree._Element) -> str:
    """Read the article title from the WeChat header, with HTML fallbacks."""

    title_nodes = source.xpath('//*[@id="activity-name"]')
    title = _text_value(title_nodes[0]) if title_nodes else ""
    if not title:
        title_nodes = source.xpath("//title")
        title = _text_value(title_nodes[0]) if title_nodes else ""
    return title or "Article"


def _base_title_from_source(source: etree._Element) -> str:
    return _clean_title(_raw_title_from_source(source)) or "Article"


def _clean_article_title_headings(article: etree._Element, base_title: str) -> None:
    # Trim the suffix across text slots so nested emphasis/colors and media
    # survive. Only headings matching this document's title are candidates.
    for node in article.iter():
        if _tag(node) not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            continue
        if _clean_title(_text_value(node)) != base_title:
            continue
        slots = [(n, attr) for kind, n, attr in _iter_content_events(node) if kind == "text"]
        value = "".join(getattr(n, attr) or "" for n, attr in slots)
        match = NOTION_TITLE_SUFFIX_RE.search(value)
        if not match:
            continue
        remaining = len(value) - match.start()
        for n, attr in reversed(slots):
            text = getattr(n, attr) or ""
            cut = min(remaining, len(text))
            if cut:
                setattr(n, attr, text[:-cut])
            remaining -= cut
            if not remaining:
                break


def _article_title_from_source(source: etree._Element) -> str:
    """Build the Sigil title as ``YYYY年M月D日_原标题`` when available."""

    base_title = _base_title_from_source(source)
    date_text = _date_text_from_source(source)
    if date_text and not base_title.startswith(f"{date_text}_"):
        return f"{date_text}_{base_title}"
    return base_title


def _author_from_source(source: etree._Element) -> str:
    """Read the account author used in the exported filename.

    WeChat snapshots expose the visible account in ``js_author_name_text``;
    older captures use ``js_author_name``.  The first non-empty value wins so
    the filename remains stable even when both nodes are present.
    """

    for element_id in ("js_author_name_text", "js_author_name"):
        value = _first_text_by_id(source, element_id)
        if value:
            return value
    author_nodes = source.xpath('//meta[translate(@name, "AUTHOR", "author")="author"]/@content')
    return author_nodes[0].strip() if author_nodes and author_nodes[0].strip() else ""


def _account_name_from_source(source: etree._Element) -> str:
    """Read the public account/display name for filename construction."""

    return _first_text_by_id(source, "js_name")


NOTION_PROPERTY_ALIASES = {
    "作者": {"作者", "作者名", "author", "作者（author）", "created by", "创建者"},
    "发布时间": {
        "发布时间",
        "发布日期",
        "发布日期（published）",
        "published",
        "publish date",
        "published date",
        "date",
        "日期",
        "创建时间",
        "created time",
    },
}


def _normalise_notion_label(value: str) -> str:
    value = _normalise_text(value).strip().rstrip(":：")
    return re.sub(r"\s+", " ", value).casefold()


def _notion_property(source: etree._Element, label: str) -> str:
    """Read a Notion property across common export label/ARIA variants."""

    wanted = {
        _normalise_notion_label(item)
        for item in NOTION_PROPERTY_ALIASES.get(label, {label})
    }
    rows = source.xpath('//*[@role="row"]')
    # A few exports retain a table but drop ARIA roles from the rows.  Restrict
    # this fallback to table descendants so ordinary article text is ignored.
    rows += source.xpath('//*[@role="table"]//tr | //*[@role="table"]/*[self::div or self::li]')
    seen: set[int] = set()
    for row in rows:
        marker = id(row)
        if marker in seen:
            continue
        seen.add(marker)
        cells = row.xpath('.//*[@role="cell"]')
        if len(cells) < 2:
            cells = row.xpath('./*[self::td or self::th or self::div]')
        values = [_text_value(cell) for cell in cells if _text_value(cell)]
        if len(values) >= 2 and _normalise_notion_label(values[0]) in wanted:
            return values[1]
    return ""


def _notion_date(value: str) -> str:
    # Notion exports may use slash dates, ISO dates, Chinese dates, or a
    # datetime suffix.  Keep one stable filename/display representation.
    value = value or ""
    match = re.search(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?", value)
    return f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日" if match else ""


def _process_type(value: str | None) -> str:
    """Return a supported processing mode, defaulting safely to WeChat."""

    value = (value or "wechat").strip().lower()
    if value not in {"wechat", "notion", "javbus"}:
        raise ValueError(f"不支持的处理类型：{value}（可选 wechat、notion 或 javbus）")
    return value


JAVBUS_TITLE_SUFFIX_RE = re.compile(r"\s*(?:[-:：|｜]\s*)?JavBus\s*$", re.I)
JAVBUS_DATE_RE = re.compile(
    r"(?:發行日期|发行日期)\s*[:：]?\s*(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})"
)

# JavBus pages are commonly saved in Traditional Chinese.  Keep this map
# local to the JavBus template so the established WeChat/Notion output stays
# byte-for-byte unchanged.  The phrase entries cover the labels used by the
# site; the character entries handle title and category text such as 最後 and
# 高畫質 without requiring an optional third-party conversion package.
JAVBUS_TRADITIONAL_TO_SIMPLIFIED = {
    "識別碼": "识别码",
    "發行日期": "发行日期",
    "長度": "长度",
    "導演": "导演",
    "製作商": "制作商",
    "發行商": "发行商",
    "類別": "类别",
    "濫交": "滥交",
    "蕩婦": "荡妇",
    "數位馬賽克": "数位马赛克",
    "羞恥": "羞耻",
    "高畫質": "高画质",
    "單體作品": "单体作品",
    "DMM獨家": "DMM独家",
    "演員": "演员",
    "磁力連結投稿": "磁力链接投稿",
    "磁力名稱": "磁力名称",
    "檔案大小": "档案大小",
    "樣品圖像": "样品图像",
    "推薦": "推荐",
    "投放廣告": "投放广告",
    "同類影片": "同类影片",
    "論壇熱帖": "论坛热帖",
    "搜尋": "搜索",
}
JAVBUS_CHAR_TRANSLATION = str.maketrans(
    {
        "萬": "万", "與": "与", "專": "专", "業": "业", "叢": "丛",
        "東": "东", "絲": "丝", "丟": "丢", "兩": "两", "嚴": "严",
        "喪": "丧", "個": "个", "豐": "丰", "臨": "临", "為": "为",
        "麗": "丽", "舉": "举", "義": "义", "烏": "乌", "樂": "乐",
        "習": "习", "鄉": "乡", "書": "书", "買": "买", "亂": "乱",
        "乾": "干", "爭": "争", "亞": "亚", "產": "产", "華": "华",
        "發": "发", "後": "后", "導": "导", "製": "制", "長": "长",
        "識": "识", "別": "别", "碼": "码", "類": "类", "蕩": "荡",
        "婦": "妇", "數": "数", "馬": "马", "賽": "赛", "恥": "耻",
        "畫": "画", "質": "质", "獨": "独", "員": "员", "連": "连",
        "結": "结", "檔": "档", "樣": "样", "圖": "图", "薦": "荐",
        "廣": "广", "論": "论", "壇": "坛", "熱": "热", "尋": "寻",
        "標": "标", "題": "题", "錄": "录", "視": "视", "頻": "频",
        "選": "选", "擇": "择", "應": "应", "會": "会", "現": "现",
        "時": "时", "間": "间", "點": "点", "開": "开", "閉": "闭",
        "頁": "页", "網": "网", "經": "经", "過": "过", "從": "从",
        "來": "来", "對": "对", "關": "关", "於": "于", "無": "无",
        "據": "据", "訊": "讯", "設": "设", "計": "计", "這": "这",
        "將": "将", "滿": "满", "進": "进", "還": "还", "遠": "远",
        "變": "变", "動": "动", "態": "态", "級": "级", "線": "线",
        "氣": "气", "鐘": "钟", "區": "区", "國": "国", "門": "门",
        "問": "问", "聞": "闻", "見": "见", "觀": "观", "覺": "觉",
        "處": "处", "務": "务", "勝": "胜", "敗": "败", "顯": "显",
        "則": "则", "轉": "转", "讓": "让", "邊": "边", "節": "节",
        "記": "记", "復": "复", "雜": "杂", "簡": "简", "爾": "尔",
        "麼": "么", "總": "总", "實": "实", "寶": "宝", "場": "场",
        "價": "价", "參": "参", "談": "谈", "驚": "惊", "愛": "爱",
        "懷": "怀", "憂": "忧", "戀": "恋", "緊": "紧", "緒": "绪",
        "續": "续", "編": "编", "纖": "纤", "組": "组", "終": "终",
        "衛": "卫", "裝": "装", "載": "载", "庫": "库", "戶": "户",
        "雲": "云", "電": "电", "腦": "脑", "機": "机", "檢": "检",
        "驗": "验", "狀": "状", "擊": "击", "繫": "系", "鏈": "链",
        "獻": "献", "獲": "获", "擁": "拥", "護": "护", "啟": "启",
        "節": "节", "麗": "丽", "銷": "销", "號": "号", "頁": "页",
    }
)


def _javbus_simplify(value: str | None) -> str:
    """Convert the visible JavBus text to Simplified Chinese."""

    text = value or ""
    # Apply longer phrases first so a phrase-specific choice (for example
    # 數位馬賽克) remains stable even when the character fallback grows.
    for traditional, simplified in sorted(
        JAVBUS_TRADITIONAL_TO_SIMPLIFIED.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(traditional, simplified)
    return text.translate(JAVBUS_CHAR_TRANSLATION)


def _javbus_simplify_tree(node: etree._Element) -> None:
    """Simplify text and human-readable attributes in a JavBus fragment."""

    if node.text:
        node.text = _javbus_simplify(node.text)
    for child in node:
        _javbus_simplify_tree(child)
        if child.tail:
            child.tail = _javbus_simplify(child.tail)
    for name in ("alt", "title"):
        if node.get(name):
            node.set(name, _javbus_simplify(node.get(name)))


def _javbus_container(source: etree._Element) -> etree._Element | None:
    """Find the content container in a JavBus SingleFile snapshot."""

    candidates = source.xpath(
        '//*[contains(concat(" ", normalize-space(@class), " "), " container ")]'
    )
    for candidate in candidates:
        if candidate.xpath('.//h3[1]') and candidate.xpath(
            './/*[contains(concat(" ", normalize-space(@class), " "), " row ") and contains(concat(" ", normalize-space(@class), " "), " movie ")]'
        ):
            return candidate
    return None


def _javbus_title_from_source(source: etree._Element) -> str:
    container = _javbus_container(source)
    title = _text_value(container.xpath('.//h3[1]')[0]) if container is not None and container.xpath('.//h3[1]') else ""
    if not title:
        title = _text_value(source.xpath('//title')[0]) if source.xpath('//title') else "Article"
    # The h3 is the work's original title and can contain Japanese orthography
    # (for example 最後).  Keep that title verbatim; Traditional Chinese in
    # the surrounding JavBus labels is simplified separately in the fragment.
    title = JAVBUS_TITLE_SUFFIX_RE.sub("", title).strip()
    return title or "Article"


def _javbus_date_from_source(source: etree._Element) -> str:
    container = _javbus_container(source)
    search_nodes = container.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " info ")]') if container is not None else []
    search_nodes = search_nodes or ([container] if container is not None else [])
    for node in search_nodes:
        match = JAVBUS_DATE_RE.search(_text_value(node))
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    # The description is a useful fallback for slight JavBus markup changes.
    for value in source.xpath('//meta[@name="description"]/@content'):
        match = JAVBUS_DATE_RE.search(value)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


def _javbus_filename_date(value: str) -> str:
    """Format an extracted ISO date for JavBus output filenames."""

    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not match:
        return value
    return f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日"


def _javbus_label_kind(node: etree._Element) -> str:
    """Return the JavBus metadata label represented by a paragraph."""

    classes = set((node.get("class") or "").lower().split())
    text = re.sub(r"[\s:：]+", "", _text_value(node))
    if "star-show" in classes or text in {"演員", "演员"}:
        return "演员"
    if text in {"類別", "类别"}:
        return "类别"
    return ""


def _javbus_hidden(node: etree._Element) -> bool:
    """Whether a JavBus node or one of its ancestors is hidden."""

    return _hidden_node(node) or any(_hidden_node(ancestor) for ancestor in node.iterancestors())


def _javbus_has_visible_actor_link(node: etree._Element) -> bool:
    return any(
        not _javbus_hidden(anchor)
        for anchor in node.xpath('.//a[contains(translate(@href, "STAR", "star"), "/star/")]')
    )


def _javbus_actor_value_node(info: etree._Element) -> etree._Element | None:
    """Locate the visible actor-value paragraph in a JavBus info column."""

    paragraphs = info.xpath(".//p")
    for index, paragraph in enumerate(paragraphs):
        if _javbus_label_kind(paragraph) != "演员":
            continue
        # Some captures put the links directly in the label paragraph.
        if _javbus_has_visible_actor_link(paragraph):
            return paragraph
        # The expanded actor list can contain a hidden <ul> between the label
        # and the visible value paragraph.  Looking only at later <p> nodes
        # skips that control without treating it as actor data.
        for candidate in paragraphs[index + 1 :]:
            if _javbus_label_kind(candidate):
                break
            if _javbus_has_visible_actor_link(candidate):
                return candidate
        # Keep a plain-text value as an explicit but unrecognisable field.  It
        # lets the filename classifier return 未识别 instead of guessing.
        for sibling in paragraph.itersiblings():
            if _tag(sibling) == "p" and not _javbus_hidden(sibling):
                return sibling
    # A markup variant may omit the label class while retaining star links.
    for paragraph in paragraphs:
        if _javbus_has_visible_actor_link(paragraph):
            return paragraph
    return None


def _javbus_actor_links(source: etree._Element) -> list[etree._Element]:
    """Return distinct, visible actor links from the selected movie row."""

    container = _javbus_container(source)
    if container is None:
        return []
    rows = container.xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " row ") and contains(concat(" ", normalize-space(@class), " "), " movie ")]'
    )
    if not rows:
        return []
    info_nodes = rows[0].xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " info ")]'
    )
    if not info_nodes:
        return []
    value_node = _javbus_actor_value_node(info_nodes[0])
    if value_node is None:
        return []
    links: list[etree._Element] = []
    seen: set[str] = set()
    for anchor in value_node.xpath(
        './/a[contains(translate(@href, "STAR", "star"), "/star/")]'
    ):
        # The actor picker and other controls can be present in the same info
        # column.  Ignore links hidden by an ancestor or by their own styles.
        if _javbus_hidden(anchor):
            continue
        href = (anchor.get("href") or "").strip()
        path = urlsplit(href).path.rstrip("/").casefold()
        key = path or href.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        links.append(anchor)
    return links


def _javbus_actor_prefix(source: etree._Element) -> str:
    """Classify a JavBus page by the number of visible actor entries."""

    count = len(_javbus_actor_links(source))
    if count == 1:
        return "单人"
    if count == 2:
        return "双人"
    if count >= 3:
        return "多人"
    return "未识别"


def _javbus_merge_metadata_blocks(
    blocks: list[etree._Element],
) -> list[etree._Element]:
    """Merge JavBus label/value paragraphs and add stable inline spacing."""

    merged: list[etree._Element] = []
    index = 0
    while index < len(blocks):
        label_block = blocks[index]
        kind = _javbus_label_kind(label_block)
        if kind and index + 1 < len(blocks) and _tag(blocks[index + 1]) == "p":
            value_block = blocks[index + 1]
            combined = etree.Element("p")
            combined.text = f"{kind}： "
            _append_text(combined, value_block.text)
            for child in value_block:
                if _tag(child) in BLOCK_TAGS:
                    _append_text(combined, _text_value(child))
                else:
                    combined.append(_inline_clone(child))
                    _append_text(combined, child.tail)
            # Normalisation intentionally drops indentation whitespace.  Add
            # one explicit separator between metadata links so it survives in
            # both XML serialization and Sigil's reflow layout.
            relevant_links = [
                anchor
                for anchor in combined.xpath('.//a[contains(translate(@href, "GENRESTAR", "genrestar"), "/genre/") or contains(translate(@href, "GENRESTAR", "genrestar"), "/star/")]')
            ]
            for anchor in relevant_links[:-1]:
                anchor.tail = (anchor.tail or "").rstrip() + " "
            merged.append(combined)
            index += 2
            continue
        merged.append(label_block)
        index += 1
    return merged


def _select_javbus_article(source: etree._Element) -> etree._Element:
    """Build the JavBus fragment: cover plus movie details only.

    JavBus places magnets, sample images, recommendations, and forum cards
    after the ``磁力連結投稿`` heading.  The requested template ends before
    that heading, so selecting the first movie row also guarantees that those
    sections cannot leak into the XHTML.
    """

    container = _javbus_container(source)
    if container is None:
        raise ValueError("未找到 JavBus 页面正文区域。")
    rows = container.xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " row ") and contains(concat(" ", normalize-space(@class), " "), " movie ")]'
    )
    if not rows:
        raise ValueError("未找到 JavBus 影片资料区域。")
    row = rows[0]
    fragment = etree.Element("div")
    # Keep only the two meaningful columns.  This drops favorite controls and
    # page navigation while preserving the embedded cover image and metadata.
    cover_nodes = row.xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " screencap ")][1]'
    )
    if cover_nodes:
        fragment.append(copy.deepcopy(cover_nodes[0]))
    info_nodes = row.xpath(
        './/*[contains(concat(" ", normalize-space(@class), " "), " info ")][1]'
    )
    if info_nodes:
        # Copy only the visible metadata paragraphs.  The info column also
        # contains a hidden actor picker (<ul>) and favorite controls, which
        # should never become blank blocks in the XHTML.
        for paragraph in info_nodes[0].xpath('.//p'):
            fragment.append(copy.deepcopy(paragraph))
    if not len(fragment):
        fragment.append(copy.deepcopy(row))
    return fragment


def _filename_title_from_source(source: etree._Element, process_type: str = "wechat") -> str:
    """Build the default output stem as ``date_account_author_title``."""

    base_title = _base_title_from_source(source)
    if process_type == "javbus":
        date_text = _javbus_filename_date(_javbus_date_from_source(source))
        base_title = _javbus_title_from_source(source)
        # Keep the source family in the filename while leaving the XHTML
        # document title clean, as requested for JavBus exports.
        parts = [_javbus_actor_prefix(source)]
        if date_text:
            parts.append(date_text)
        parts.append(f"{base_title} - JavBus")
        return "_".join(parts)
    if process_type == "notion":
        date_text = _notion_date(_notion_property(source, "发布时间"))
        account = ""
        author = _notion_property(source, "作者")
    else:
        date_text = _date_text_from_source(source)
        account = _account_name_from_source(source)
        author = _author_from_source(source)
    parts = [part for part in (date_text, account, author, base_title) if part]
    return "_".join(parts) or "Article"


def _article_title_from_path(input_path: Path) -> str:
    """Infer the output title for CLI naming without changing conversion flow."""

    try:
        raw = input_path.read_bytes()
        source = html.fromstring(raw, parser=html.HTMLParser(encoding="utf-8"))
        return _article_title_from_source(source)
    except Exception:  # noqa: BLE001 - conversion reports the detailed error later
        return input_path.stem or "Article"


def _filename_title_from_path(input_path: Path, process_type: str = "wechat") -> str:
    """Infer the date/author/title output stem for CLI default naming."""

    try:
        raw = input_path.read_bytes()
        source = html.fromstring(raw, parser=html.HTMLParser(encoding="utf-8"))
        return _filename_title_from_source(source, _process_type(process_type))
    except Exception:  # noqa: BLE001 - conversion reports the detailed error later
        return input_path.stem or "Article"


def _select_article(source: etree._Element, process_type: str = "wechat") -> etree._Element:
    """Find the article, repairing a common malformed #js_content wrapper."""

    if process_type == "javbus":
        return _select_javbus_article(source)

    # Newer Notion/SingleFile exports put the property panel and the actual
    # page blocks under the same ``main`` element, but keep the article itself
    # in ``.notion-page-content``.  Selecting the page-content node prevents
    # UI chrome such as “more properties” and “Add 标签” from leaking into the
    # XHTML.  Older captures may omit that class, so the broader fallbacks
    # below remain in place.
    if process_type == "notion":
        page_content = source.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), " notion-page-content ")]'
        )
        if page_content:
            return page_content[0]

    matches = source.xpath('//*[@id="js_content"]')
    if matches:
        candidate = matches[0]
        if len(candidate) or _text_value(candidate):
            return candidate
        # HTML parsers close <h1>/<h2> before a nested <p>.  Some SingleFile
        # snapshots therefore leave an empty #js_content heading followed by
        # the real article blocks as siblings.  Reassemble that range.
        parent = candidate.getparent()
        if parent is not None:
            repaired = etree.Element("div")
            start = parent.index(candidate) + 1
            for sibling in list(parent)[start:]:
                repaired.append(copy.deepcopy(sibling))
            if len(repaired):
                return repaired
        return candidate
    for expression in ("//article", "//main", "//*[@role='main']", "//body"):
        candidates = source.xpath(expression)
        if candidates:
            return candidates[0]
    raise ValueError("未找到可转换的正文区域。")


def _strip_notion_property_panel(article: etree._Element) -> None:
    """Remove Notion's property table while preserving article media."""

    for table in article.xpath('.//*[@role="table"]'):
        property_labels = {
            _normalise_notion_label(value)
            for values in NOTION_PROPERTY_ALIASES.values()
            for value in values
        }
        row_labels = {
            _normalise_notion_label(_text_value(cell))
            for row in table.xpath('.//*[@role="row"]')
            for cell in row.xpath('.//*[@role="cell"]')[:1]
        }
        if row_labels & property_labels:
            parent = table.getparent()
            if parent is not None:
                parent.remove(table)

    # Some exports put the collapsed property controls beside the table and
    # omit ARIA roles. Remove only unmistakable Notion chrome blocks; article
    # paragraphs with ordinary words such as “关联” remain untouched.
    chrome_re = re.compile(r"^(?:其他\s*\d+\s*个属性|更多属性|\d+\s*more\s*properties|关联|添加\s*标签|add\s*标签)$", re.I)
    for node in list(article.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " notion-page-block ")]')):
        text = _text_value(node)
        if chrome_re.match(text):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
    for node in list(article.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " notion-topbar ")] | .//*[contains(concat(" ", normalize-space(@class), " "), " notion-sidebar ")]')):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)


def _insert_text_before(parent: etree._Element, index: int, value: str) -> None:
    if not value:
        return
    if index == 0:
        parent.text = (parent.text or "") + value
    else:
        previous = parent[index - 1]
        previous.tail = (previous.tail or "") + value


def _remove_preserving_tail(node: etree._Element) -> None:
    parent = node.getparent()
    if parent is None:
        return
    index = parent.index(node)
    _insert_text_before(parent, index, node.tail or "")
    parent.remove(node)


def _unwrap(node: etree._Element) -> None:
    """Remove a layout wrapper while preserving text, children, and tails."""

    parent = node.getparent()
    if parent is None:
        return
    index = parent.index(node)
    children = list(node)
    _insert_text_before(parent, index, node.text or "")
    for child in children:
        node.remove(child)
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    if node.tail:
        if children:
            children[-1].tail = (children[-1].tail or "") + node.tail
        else:
            _insert_text_before(parent, index, node.tail)
    parent.remove(node)


def _hidden_node(node: etree._Element) -> bool:
    style = node.attrib.get("style", "")
    classes = node.attrib.get("class", "").lower().split()
    aria_hidden = node.attrib.get("aria-hidden", "").lower()
    return (
        bool(re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I))
        or any(item in {"sf-hidden", "hidden"} for item in classes)
        or aria_hidden == "true"
    )


def _is_centered_source(node: etree._Element) -> bool:
    """Read centering before presentation attributes are discarded."""

    style = node.attrib.get("style", "")
    align = node.attrib.get("align", "").lower()
    classes = node.attrib.get("class", "").lower().split()
    return (
        bool(re.search(r"(?:^|;)\s*text-align\s*:\s*center\b", style, re.I))
        or align == "center"
        or any(item in {"center", "text-center"} for item in classes)
    )


def _presentation_semantic_tag(node: etree._Element) -> str | None:
    """Turn a styled layout span into a small semantic inline element."""

    if _tag(node) not in {"span", "font"}:
        return None
    # A styled wrapper around a section/div is still a layout container.  Do
    # not turn it into <strong>/<em>, otherwise the block would become nested
    # inside an inline element and could no longer be split into paragraphs.
    if any(_tag(descendant) in BLOCK_TAGS for descendant in node.iterdescendants()):
        return None
    style = node.attrib.get("style", "")
    if re.search(r"(?:^|;)\s*font-weight\s*:\s*(?:bold|[6-9]00)", style, re.I):
        return "strong"
    if re.search(r"(?:^|;)\s*font-style\s*:\s*(?:italic|oblique)", style, re.I):
        return "em"
    if re.search(r"(?:^|;)\s*text-decoration\s*:\s*[^;]*underline", style, re.I):
        return "u"
    return None


NOTION_COLOR_MAP = {
    "--c-redtexsec": "#d44c47",
    "--c-redtexpri": "#d44c47",
    "--c-blutexsec": "#337ea9",
    "--c-blutexpri": "#337ea9",
    "--c-gretexpri": "#448361",
    "--c-yeltexpri": "#cb912f",
    "--c-texaccpri": "#0b6e69",
}


def _source_color(node: etree._Element) -> str:
    """Extract a portable text color from a Notion/HTML inline node."""

    highlight = (node.get("data-notion-highlight") or "").strip().lower()
    if highlight in {"red", "blue", "green", "yellow", "orange", "pink", "purple"}:
        return {"red": "#d44c47", "blue": "#337ea9", "green": "#448361", "yellow": "#cb912f", "orange": "#d9730d", "pink": "#c14c8a", "purple": "#9065b0"}[highlight]
    value = node.get("color") or ""
    if not value:
        match = re.search(r"(?:^|;)\s*color\s*:\s*([^;]+)", node.get("style", ""), re.I)
        value = match.group(1).strip() if match else ""
    value = value.strip()
    if value.lower().startswith("var("):
        variable = re.search(r"--[a-z0-9_-]+", value, re.I)
        value = NOTION_COLOR_MAP.get(variable.group(0).lower(), "") if variable else ""
    if not value or value.lower() in {"inherit", "currentcolor", "transparent", "initial", "unset"}:
        return ""
    if re.fullmatch(r"#[0-9a-f]{3,8}|(?:rgb|hsl)a?\([^)]*\)|[a-z]{3,20}", value, re.I):
        return value
    return ""


def _mark_color_annotations(article: etree._Element) -> None:
    for node in article.iter():
        color = _source_color(node)
        if color:
            node.set(INTERNAL_COLOR_ATTR, color)


def _sanitise_tree(node: etree._Element) -> int:
    """Strip browser/editor artefacts and flatten presentation wrappers."""

    removed = 0
    if _is_centered_source(node):
        node.set(INTERNAL_CENTER_ATTR, "1")
    for child in list(node):
        if not isinstance(child.tag, str):
            _remove_preserving_tail(child)
            removed += 1
            continue
        tag = _tag(child)
        if tag in REMOVE_TAGS or (_hidden_node(child) and not (tag == "img" and image_source(child))):
            _remove_preserving_tail(child)
            removed += 1
            continue
        if tag == "wbr":
            # <wbr> is an optional browser break, not an author-entered line
            # break.  Dropping it keeps the output portable across Sigil and
            # EPUB readers; explicit <br> elements are handled as reflowable
            # spaces or vertical spacing below.
            _remove_preserving_tail(child)
            removed += 1
            continue
        if _is_centered_source(child):
            # Keep this as an internal marker until section detection has run.
            # _to_xhtml deliberately does not copy it to the output.
            child.set(INTERNAL_CENTER_ATTR, "1")
        if tag == "img":
            # SingleFile and Notion exports may keep the real image URL in a
            # lazy-loading field, while src is blank/about:blank or absent.
            source = image_source(child)
            if source:
                child.set("src", source)
        semantic_tag = _presentation_semantic_tag(child)
        if semantic_tag:
            child.tag = semantic_tag
            tag = semantic_tag
        removed += _sanitise_tree(child)

        for name in list(child.attrib):
            lowered = name.lower()
            value = child.attrib[name]
            if (
                lowered not in ALLOWED_ATTRIBUTES
                and lowered not in {INTERNAL_CENTER_ATTR, INTERNAL_COLOR_ATTR}
                or lowered.startswith("on")
                or lowered in {"href", "src"}
                and not safe_url(lowered, value)
            ):
                del child.attrib[name]

        # A span/font/center has no document meaning after its visual style is
        # removed.  Flatten it; this also exposes malformed section elements
        # which were placed inside a paragraph by a web editor.
        if tag in INLINE_WRAPPER_TAGS and any(
            _tag(descendant) in BLOCK_TAGS for descendant in child.iterdescendants()
        ):
            # Invalid snapshots sometimes put a whole section or paragraph
            # inside <strong>/<em>/<a>.  Flatten that wrapper so each block can
            # be emitted as its own XHTML element.
            _unwrap(child)
            continue
        if (tag == "span" or tag in {"font", "center"} or tag not in (
            SEMANTIC_INLINE_TAGS | BLOCK_TAGS | {"br", "img", "ruby", "rt", "rp"}
        )) and not child.get(INTERNAL_COLOR_ATTR):
            _unwrap(child)
    return removed


def _append_text(parent: etree._Element, value: str | None) -> None:
    raw = value or ""
    value = _normalise_text(raw)
    # Whitespace-only nodes between block elements are parser indentation, not
    # an author paragraph.  Keep spaces inside real text runs, however.
    if not value or not value.strip():
        # A literal ASCII space between two inline runs is content (for
        # example, <strong>Hello</strong> <em>world</em>).  Newline/tab-only
        # indentation from SingleFile is still discarded.
        if (
            value
            and (len(parent) or (parent.text and parent.text.strip()))
            and not re.search(r"[\t\r\n\f]", raw)
            and raw.replace("\xa0", "").strip() == ""
        ):
            if len(parent):
                last = parent[-1]
                last.tail = (last.tail or "") + " "
            else:
                parent.text = (parent.text or "") + " "
        return
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + value
    else:
        parent.text = (parent.text or "") + value


def _inline_clone(node: etree._Element) -> etree._Element:
    """Clone a sanitised inline node without reintroducing source CSS."""

    tag = _tag(node)
    clone = etree.Element(tag or "span")
    for name, value in node.attrib.items():
        lowered = name.lower()
        if lowered == INTERNAL_COLOR_ATTR:
            clone.set(INTERNAL_COLOR_ATTR, value)
        elif lowered in ALLOWED_ATTRIBUTES and not lowered.startswith("on"):
            if lowered not in {"href", "src"} or safe_url(lowered, value):
                clone.set(lowered, value)
    clone.text = _normalise_text(node.text) or None
    for child in node:
        # Sanitisation has already flattened layout spans.  If an unusual
        # source still leaves a block here, retain its textual content rather
        # than emitting invalid inline structure.
        if _tag(child) in BLOCK_TAGS:
            _append_text(clone, _text_value(child))
            if child.tail:
                _append_text(clone, child.tail)
            continue
        nested = _inline_clone(child)
        clone.append(nested)
        nested.tail = _normalise_text(child.tail)
    return clone


def _append_inline(parent: etree._Element, child: etree._Element) -> None:
    tag = _tag(child)
    if tag in BLOCK_TAGS:
        _append_text(parent, _text_value(child))
        return
    clone = _inline_clone(child)
    clone.tail = None
    parent.append(clone)
    _append_text(parent, child.tail)


def _trim_block_edges(block: etree._Element) -> None:
    if block.text:
        block.text = block.text.lstrip()
    if len(block):
        last = block[-1]
        if last.tail:
            last.tail = last.tail.rstrip()
    elif block.text:
        block.text = block.text.rstrip()


def _is_spacer(paragraph: etree._Element) -> bool:
    """Whether a block contains only explicit line breaks and wrappers."""

    found_break = False

    def visit(node: etree._Element) -> bool:
        nonlocal found_break
        if node.text and node.text.strip():
            return False
        for child in node:
            tag = _tag(child)
            if tag == "br":
                found_break = True
            elif tag in INLINE_WRAPPER_TAGS | {"ruby", "rt", "rp"}:
                if not visit(child):
                    return False
            else:
                # Images, nested blocks, and other content-bearing elements
                # must never be silently replaced by a spacer.
                return False
            if child.tail and child.tail.strip():
                return False
        return True

    valid = visit(paragraph)
    return valid and found_break


def _iter_content_events(node: etree._Element):
    """Yield element starts and text slots in document order.

    ``lxml`` stores text before the first child in ``element.text`` and text
    after a child in ``child.tail``.  Walking those slots explicitly lets the
    break normaliser inspect the characters on either side of a nested ``br``
    without serialising or otherwise disturbing the source tree.
    """

    yield ("start", node, None)
    if node.text is not None:
        yield ("text", node, "text")
    for child in node:
        yield from _iter_content_events(child)
        if child.tail is not None:
            yield ("text", child, "tail")
    yield ("end", node, None)


def _slot_value(event: tuple[str, etree._Element, str | None]) -> str:
    if event[0] != "text" or event[2] is None:
        return ""
    return getattr(event[1], event[2]) or ""


BREAK_BOUNDARY_TAGS = {
    "audio",
    "canvas",
    "embed",
    "figure",
    "hr",
    "iframe",
    "img",
    "object",
    "table",
    "video",
}


def _is_break_boundary_event(event) -> bool:
    """Whether an event is an atomic element that text search must not cross."""

    return event[0] in {"start", "end"} and _tag(event[1]) in BREAK_BOUNDARY_TAGS


def _last_non_space(events) -> str:
    for event in reversed(events):
        if _is_break_boundary_event(event):
            return ""
        value = _slot_value(event)
        match = re.search(r"(\S)\s*$", value)
        if match:
            return match.group(1)
    return ""


def _first_non_space(events) -> str:
    for event in events:
        if _is_break_boundary_event(event):
            return ""
        value = _slot_value(event)
        match = re.search(r"\S", value)
        if match:
            return match.group(0)
    return ""


def _is_cjk_character(value: str) -> bool:
    if not value:
        return False
    codepoint = ord(value)
    return (
        0x3400 <= codepoint <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= codepoint <= 0x9FFF  # Common CJK ideographs
        or 0xF900 <= codepoint <= 0xFAFF  # CJK compatibility ideographs
        or 0x20000 <= codepoint <= 0x323AF  # CJK Unified Ideographs extensions
        or 0x3040 <= codepoint <= 0x30FF  # Hiragana, Katakana
        or 0xAC00 <= codepoint <= 0xD7AF  # Hangul syllables
    )


def _is_latin_or_digit(value: str) -> bool:
    """Whether a character can participate in an alphabetic/number word."""

    # ``str.isalpha`` also covers accented Latin and other alphabetic scripts;
    # CJK, kana, and Hangul have already been excluded above because they do
    # not use spaces between ordinary characters in reflowed prose.
    return bool(value) and not _is_cjk_character(value) and (
        value.isalpha() or value.isdigit()
    )


def _break_separator(left: str, right: str) -> str:
    """Choose the separator for a source visual break.

    CJK text does not use inter-word spaces, so a break between two CJK
    characters is removed outright.  ASCII/Latin and numeric word runs need a
    space to avoid concatenating words when a browser line break was captured
    as ``<br>``.  Mixed CJK/Latin boundaries are left tight as well; this is
    the least surprising typography for Chinese prose containing inline
    English or numbers.
    """

    if not left or not right:
        return ""
    if _is_cjk_character(left) and _is_cjk_character(right):
        return ""
    if _is_latin_or_digit(left) and _is_latin_or_digit(right):
        return " "
    return ""


def _trim_break_adjacent_whitespace(events, break_index: int, direction: int) -> None:
    """Remove layout whitespace immediately adjacent to a break."""

    indices = range(break_index - 1, -1, -1) if direction < 0 else range(
        break_index + 1, len(events)
    )
    for index in indices:
        event = events[index]
        if _is_break_boundary_event(event):
            return
        if event[0] != "text":
            continue
        value = _slot_value(event)
        if not value:
            continue
        trimmed = value.rstrip() if direction < 0 else value.lstrip()
        setattr(event[1], event[2], trimmed)
        # Continue past a whitespace-only slot; stop after the first content
        # slot so intentional spaces elsewhere in the paragraph remain intact.
        if trimmed:
            break


def _append_break_separator(events, break_index: int, separator: str) -> None:
    if not separator:
        return
    for index in range(break_index - 1, -1, -1):
        event = events[index]
        if _is_break_boundary_event(event):
            break
        if event[0] != "text":
            continue
        value = _slot_value(event)
        if value:
            setattr(event[1], event[2], value + separator)
            return
    for index in range(break_index + 1, len(events)):
        event = events[index]
        if _is_break_boundary_event(event):
            break
        if event[0] != "text":
            continue
        value = _slot_value(event)
        if value:
            setattr(event[1], event[2], separator + value)
            return


def _flatten_breaks(node: etree._Element, root: etree._Element | None = None) -> None:
    """Remove source hard breaks while preserving natural word boundaries."""

    if root is None:
        root = node
    for child in list(node):
        if _tag(child) == "br":
            events = list(_iter_content_events(root))
            break_index = next(
                (
                    index
                    for index, event in enumerate(events)
                    if event[0] == "start" and event[1] is child
                ),
                None,
            )
            if break_index is not None:
                left = _last_non_space(events[:break_index])
                right = _first_non_space(events[break_index + 1 :])
                _trim_break_adjacent_whitespace(events, break_index, -1)
                _trim_break_adjacent_whitespace(events, break_index, 1)
                _append_break_separator(events, break_index, _break_separator(left, right))
            _remove_preserving_tail(child)
            continue
        _flatten_breaks(child, root)


def _prune_empty_inline(node: etree._Element) -> None:
    """Remove empty formatting wrappers left after hard-break flattening."""

    for child in list(node):
        _prune_empty_inline(child)
        if (
            _tag(child) in SEMANTIC_INLINE_TAGS
            and not _text_value(child)
            and not child.xpath(".//img")
        ):
            # Keep whitespace carried by the wrapper.  A flattened ``<br>``
            # can leave a semantic element containing only a separator
            # (for example ``foo<strong><br/></strong>bar``); removing the
            # element outright would incorrectly concatenate the two runs.
            # Unwrapping preserves its text and tail, while the paragraph
            # edge trim below still removes whitespace-only boundary nodes.
            _unwrap(child)


def _finish_paragraph(
    paragraph: etree._Element,
    heading_counter: list[int],
    source: etree._Element | None = None,
) -> list[etree._Element]:
    # Break-only blocks represent vertical spacing.  In prose, however, a
    # source <br> is a hard layout hint; flatten it to a space before emitting
    # the paragraph so the reader can reflow lines at any width.
    if _is_spacer(paragraph):
        spacer = etree.Element("div", attrib={"class": "spacer"})
        spacer.text = "\xa0"
        return [spacer]
    _flatten_breaks(paragraph)
    _prune_empty_inline(paragraph)
    _trim_block_edges(paragraph)
    centered = bool(paragraph.get(INTERNAL_CENTER_ATTR))
    if source is not None and _tag(source) in {"p", "section"}:
        centered = centered or (
            source.get(INTERNAL_CENTER_ATTR) == "1"
            and not any(_tag(descendant) in BLOCK_TAGS for descendant in source)
        )
    paragraph_text = _text_value(paragraph)
    if (
        paragraph_text
        and len(paragraph_text) <= SECTION_TITLE_MAX_LEN
        and not PLAIN_NUMBERED_RE.match(paragraph_text)
        and (SECTION_TITLE_RE.match(paragraph_text) or centered)
    ):
        heading_counter[0] += 1
        heading = etree.Element(
            "h2",
            attrib={"class": "section-title", "id": f"section-{heading_counter[0]}"},
        )
        heading.text = paragraph.text
        for child in paragraph:
            paragraph.remove(child)
            heading.append(child)
        _trim_block_edges(heading)
        return [heading]
    if not len(paragraph) and not _text_value(paragraph):
        return []
    if not _text_value(paragraph) and not paragraph.xpath(".//img"):
        return []
    return [paragraph]


def _normalise_paragraph(
    node: etree._Element, heading_counter: list[int]
) -> list[etree._Element]:
    output: list[etree._Element] = []
    paragraph = etree.Element("p")
    _append_text(paragraph, node.text)
    for child in node:
        tag = _tag(child)
        if tag in BLOCK_TAGS:
            output.extend(_finish_paragraph(paragraph, heading_counter, node))
            output.extend(_normalise_block(child, heading_counter))
            paragraph = etree.Element("p")
            _append_text(paragraph, child.tail)
        else:
            _append_inline(paragraph, child)
    output.extend(_finish_paragraph(paragraph, heading_counter, node))
    return output


def _normalise_container(
    node: etree._Element, heading_counter: list[int]
) -> list[etree._Element]:
    output: list[etree._Element] = []
    paragraph = etree.Element("p")
    _append_text(paragraph, node.text)
    for child in node:
        if _tag(child) in BLOCK_TAGS:
            output.extend(_finish_paragraph(paragraph, heading_counter, node))
            output.extend(_normalise_block(child, heading_counter))
            paragraph = etree.Element("p")
            _append_text(paragraph, child.tail)
        else:
            _append_inline(paragraph, child)
    output.extend(_finish_paragraph(paragraph, heading_counter, node))
    return output


def _is_section_title(node: etree._Element) -> bool:
    text = _text_value(node)
    if not text or len(text) > SECTION_TITLE_MAX_LEN:
        return False
    # Numbered sections are the reliable signal in WeChat/SingleFile exports.
    # Other sections become headings only when the source explicitly centered
    # them; ordinary layout sections must remain paragraph containers.
    return (
        not PLAIN_NUMBERED_RE.match(text)
        and (bool(SECTION_TITLE_RE.match(text)) or node.get(INTERNAL_CENTER_ATTR) == "1")
    )


def _section_heading(node: etree._Element, index: int) -> etree._Element:
    heading = etree.Element("h2", attrib={"class": "section-title", "id": f"section-{index}"})
    _append_text(heading, node.text)
    for child in node:
        if _tag(child) in BLOCK_TAGS:
            _append_text(heading, _text_value(child))
        else:
            _append_inline(heading, child)
    _trim_block_edges(heading)
    return heading


def _normalise_block(node: etree._Element, heading_counter: list[int]) -> list[etree._Element]:
    tag = _tag(node)
    if tag == "p":
        return _normalise_paragraph(node, heading_counter)
    if tag == "section":
        if _is_section_title(node):
            heading_counter[0] += 1
            return [_section_heading(node, heading_counter[0])]
        return _normalise_container(node, heading_counter)
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        # Some snapshots use a heading tag for every numbered block.  Chinese
        # ``1、正文`` entries are prose lists, so demote them to a reflowable
        # paragraph even when the source tag says h2/h3.
        if PLAIN_NUMBERED_RE.match(_text_value(node)):
            return _normalise_paragraph(node, heading_counter)
        heading_tag = "h1" if tag == "h1" else "h2"
        heading = etree.Element(heading_tag, attrib={"class": "section-title"})
        _append_text(heading, node.text)
        for child in node:
            _append_inline(heading, child)
        _trim_block_edges(heading)
        # Malformed SingleFile captures can leave an empty heading shell
        # before the actual article.  Do not emit a blank navigation target.
        if not _text_value(heading) and not heading.xpath(".//img"):
            return []
        return [heading]
    if tag == "img":
        paragraph = etree.Element("p", attrib={"class": "image"})
        clone = _inline_clone(node)
        paragraph.append(clone)
        return [paragraph]
    if tag == "hr":
        return [copy.deepcopy(node)]
    if tag in {"ul", "ol", "dl", "table", "figure", "pre"}:
        return [copy.deepcopy(node)]
    return _normalise_container(node, heading_counter)


def _normalise_article(article: etree._Element) -> tuple[list[etree._Element], int]:
    """Return clean block elements and count removed page controls."""

    removed = _sanitise_tree(article)
    blocks: list[etree._Element] = []
    counter = [0]
    # Treat the selected article itself as a container.  Keeping its original
    # id/class wrapper would reintroduce webpage-specific layout semantics.
    raw_blocks = _normalise_container(article, counter)
    for block in raw_blocks:
        if (
            block.get("class") == "spacer"
            and blocks
            and blocks[-1].get("class") == "spacer"
        ):
            continue
        blocks.append(block)
    # Keep code/pre blocks untouched, but make all prose blocks genuinely
    # reflowable even when a source heading or list carried a stray <br>.
    for block in blocks:
        if _tag(block) != "pre":
            _flatten_breaks(block)
            _prune_empty_inline(block)
    # A direct <h2> source heading does not go through the section branch, so
    # assign it an ID here for a stable Sigil table of contents.
    for heading in (item for item in blocks if _tag(item) == "h2"):
        if not heading.get("id"):
            counter[0] += 1
            heading.set("id", f"section-{counter[0]}")
    return blocks, removed


def _to_xhtml(node: etree._Element, namespace: str) -> etree._Element:
    tag = _tag(node) or "span"
    output = etree.Element(f"{{{namespace}}}{tag}")
    for name, value in node.attrib.items():
        lowered = name.lower()
        if lowered == INTERNAL_COLOR_ATTR:
            output.set("style", f"color:{value}")
            continue
        # Width/height are meaningful for images, but on a prose container
        # they can reintroduce the fixed layout copied from a web page.
        # Keep only the image dimensions; the generated stylesheet constrains
        # images to the available reading width.
        dimension_allowed = lowered in {"width", "height"} and tag == "img"
        if lowered in {"class", "id"} or (
            lowered in ALLOWED_ATTRIBUTES
            and (lowered not in {"width", "height"} or dimension_allowed)
        ):
            output.set(lowered, value)
    output.text = node.text
    for child in node:
        converted = _to_xhtml(child, namespace)
        output.append(converted)
        converted.tail = child.tail
    return output


def _image_report(root: etree._Element) -> dict[str, int]:
    """Summarise image retention and offline-risk indicators."""

    images = root.xpath('.//*[local-name()="img"]')
    embedded = 0
    external = 0
    missing = 0
    for image in images:
        src = (image.get("src") or "").strip().lower()
        if not src:
            missing += 1
        elif src.startswith("data:image/"):
            embedded += 1
        elif src.startswith(("http://", "https://")):
            external += 1
    return {"images": len(images), "embedded": embedded, "external": external, "missing": missing}


def convert(
    input_path: Path,
    output_path: Path,
    process_type: str = "wechat",
    preserve_colors: bool = False,
) -> tuple[int, int, str]:
    process_type = _process_type(process_type)
    raw = input_path.read_bytes()
    source = html.fromstring(raw, parser=html.HTMLParser(encoding="utf-8"))
    article = _select_article(source, process_type)

    if process_type == "javbus":
        title = _javbus_title_from_source(source)
    elif process_type == "wechat":
        title = _article_title_from_source(source)
    else:
        notion_date_text = _notion_date(_notion_property(source, "发布时间"))
        base_title = _base_title_from_source(source)
        title = f"{notion_date_text}_{base_title}" if notion_date_text else base_title

    # Normalisation sanitises its input tree in place.  Keep the parsed source
    # intact so Notion properties (作者/发布时间) remain available for the
    # metadata line and output filename after the article has been cleaned.
    article_copy = copy.deepcopy(article)
    if process_type == "javbus":
        _javbus_simplify_tree(article_copy)
    _clean_article_title_headings(article_copy, _base_title_from_source(source))
    if preserve_colors:
        _mark_color_annotations(article_copy)
    if process_type == "notion":
        _strip_notion_property_panel(article_copy)
    base_values = source.xpath("//base/@href")
    base_href = str(base_values[0]).strip() if base_values else ""
    if not base_href:
        # 没有 <base href> 时用 SingleFile 记录的原始页面地址，保证图片下载带正确 Referer
        base_href = _singlefile_page_url(source)
    image_embedding = _embed_external_images(article_copy, base_href, _notion_public_origin(source))
    blocks, removed_nodes = _normalise_article(article_copy)
    if process_type == "javbus":
        blocks = _javbus_merge_metadata_blocks(blocks)
    if process_type == "notion":
        notion_raw_date = _notion_property(source, "发布时间")
        notion_dates = {notion_raw_date, _notion_date(notion_raw_date)}
        cleaned_blocks: list[etree._Element] = []
        for block in blocks:
            block_text = _text_value(block)
            # Different Notion/SingleFile exports can turn the page title into
            # h1, h2, p, or a layout div.  The exact block text is the stable
            # signal, so remove the duplicate title regardless of tag.
            if block_text in {_base_title_from_source(source), _raw_title_from_source(source)} and not block.xpath(".//img"):
                continue
            if block_text in notion_dates:
                if _tag(block) == "p":
                    block.set("class", "body-text")
                    cleaned_blocks.append(block)
                    continue
                paragraph = etree.Element("p")
                _append_text(paragraph, block.text)
                for child in block:
                    _append_inline(paragraph, child)
                cleaned_blocks.append(paragraph)
            else:
                cleaned_blocks.append(block)
        blocks = cleaned_blocks

    # Resolve Notion's private attachment URLs while running locally. This is
    # the reliable path for producing a self-contained XHTML file.

    ns = "http://www.w3.org/1999/xhtml"
    root = etree.Element(f"{{{ns}}}html", nsmap={None: ns})
    head = etree.SubElement(root, f"{{{ns}}}head")
    meta = etree.SubElement(head, f"{{{ns}}}meta")
    meta.set("http-equiv", "Content-Type")
    meta.set("content", "application/xhtml+xml; charset=UTF-8")
    title_element = etree.SubElement(head, f"{{{ns}}}title")
    title_element.text = title
    style = etree.SubElement(head, f"{{{ns}}}style")
    style.set("type", "text/css")
    style.text = BOOK_CSS
    body = etree.SubElement(root, f"{{{ns}}}body")
    has_title_heading = any(
        _tag(block) in {"h1", "h2"} and _text_value(block) == title for block in blocks
    )
    if not has_title_heading:
        title_heading = etree.SubElement(body, f"{{{ns}}}h1")
        title_heading.text = title
    if process_type == "javbus":
        metadata = []
    elif process_type == "wechat":
        metadata = _extract_article_meta(source)
    else:
        metadata = [
            v for v in (_notion_property(source, "作者"), _notion_property(source, "发布时间")) if v
        ]
    if metadata:
        meta_block = etree.SubElement(body, f"{{{ns}}}p", attrib={"class": "article-meta"})
        meta_block.text = " · ".join(metadata)
    for block in blocks:
        body.append(_to_xhtml(block, ns))

    tree = etree.ElementTree(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Write through a file handle: lxml/libxml2 treats a path containing "%" as
    # a URI and re-encodes it ("%"→"%25") on write, while parse() below looks
    # up the literal name — the mismatch raised OSError and left the source
    # half-processed, then tidy_sources() re-wrote it under yet another name
    # so one article quietly became two files (verified 2026-09-16).
    with open(output_path, "wb") as fh:
        tree.write(
            fh,
            encoding="UTF-8",
            xml_declaration=True,
            doctype=DOCTYPE,
            # Do not pretty-print the tree.  lxml's formatter inserts indentation
            # around inline elements, turning layout whitespace into text (for
            # example, splitting a heading into ``0/`` and ``前言``).  XHTML is
            # still valid XML without that presentation formatting, and block
            # elements remain naturally reflowable in Sigil/readers.
            pretty_print=False,
        )

    # Use the same XML-level guarantee Sigil needs before reporting success.
    etree.parse(str(output_path), etree.XMLParser(resolve_entities=False, no_network=True))
    paragraph_count = len(
        body.xpath(".//*[local-name()='p' and not(@class='spacer') and not(contains(concat(' ', normalize-space(@class), ' '), ' article-meta '))]")
    )
    return paragraph_count, removed_nodes, title


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 SingleFile HTML 网页快照转换成可导入 Sigil 的 XHTML。"
    )
    parser.add_argument("input", type=Path, help="SingleFile HTML 文件路径")
    parser.add_argument("-o", "--output", type=Path, help="输出 XHTML 路径")
    parser.add_argument(
        "--type",
        choices=("wechat", "notion", "javbus"),
        default="wechat",
        help="处理类型：wechat（微信公众号，默认）、notion（Notion 文档）或 javbus（JavBus 页面）",
    )
    parser.add_argument(
        "--preserve-colors",
        action="store_true",
        help="保留正文中的文字颜色标注（默认关闭）",
    )
    args = parser.parse_args()

    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        print(f"输入文件不存在：{input_path}", file=sys.stderr)
        return 2
    if args.output:
        output_path = args.output.expanduser().resolve()
    else:
        output_path = default_output(
            input_path,
            _filename_title_from_path(input_path, args.type),
        ).expanduser().resolve()
    try:
        paragraphs, removed, title = convert(input_path, output_path, args.type, args.preserve_colors)
    except Exception as exc:  # noqa: BLE001 - present a useful GUI/CLI error to the user.
        print(f"转换失败：{exc}", file=sys.stderr)
        return 1

    print(f"转换完成：{output_path}")
    print(f"标题：{title}")
    print(f"正文段落：{paragraphs}；移除页面控件：{removed}")
    report = _image_report(etree.parse(str(output_path), etree.XMLParser(resolve_entities=False, no_network=True)).getroot())
    image_note = f"图片：{report['images']} 张（内嵌 {report['embedded']}，外链 {report['external']}"
    if report["missing"]:
        image_note += f"，缺少地址 {report['missing']}"
    print(image_note + "）")
    if report["external"]:
        print("提示：外链图片可能受原网站链接有效期影响；内嵌图片已写入 XHTML。")
    print(f"颜色标注：{'已保留' if args.preserve_colors else '未保留'}")
    print("本工具只生成自然重排 XHTML，不生成 EPUB；现在可以在 Sigil 中选择“添加现有文件”继续制作 EPUB。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
