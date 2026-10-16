#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页存档（WordPress 博客）来源适配器。

为什么要有它（陈少 2026-10-16 定）
    猫笔刀等博主除了微信公众号，还有自己的独立站（WordPress，如 maobidao.cn）。
    这些站点文章会被浏览器「保存网页」成单个 html 存档，带 ``<base href>``、
    ``<meta download_date>`` 和 WordPress 的 ``entry-content`` 正文容器。
    它跟微信直取的 html 是两套结构，但陈少要的是**后端产出同一种 XHTML/EPUB**。

    所以本适配器只做一件事——把 WordPress 存档结构翻译成跟 WeChatAdapter
    完全相同的 RawDoc，然后调用**同一个**渲染入口（sigi_convert）。
    产出一致性由 sigi_convert 保证，本文件不复制任何排版规则。

怎么认出它
    WordPress 存档特征：``<base href="https://站点/.../">`` + ``entry-content``
    正文容器（或 ``wp-block-`` 前缀的正文段落）。命中即认为这是网页存档。

字段映射（WordPress 结构 → 跟微信同名的字段）
    作者      → ``<span class="author vcard">`` 里的 itemprop=name
    日期      → ``<time datetime=...>`` 或 meta article:published_time
    链接      → ``<base href>``（存档原始地址）
    正文      → ``<div class="entry-content">``
"""

from __future__ import annotations

import datetime as dt
import html as html_mod
import re
from pathlib import Path

from ..pipeline import RawDoc, register

# WordPress 网页存档特征：base href（SingleFile/浏览器存档都会写）+ entry-content 正文容器。
MARKS = ("entry-content", "wp-block-", "<base href=")
# 微信文章被浏览器「另存为」/存档时，同样会有 <base href>（指向 mp.weixin.qq.com），
# 但它们带微信专属 id/class（activity-name、js_content、js_name、publish_time）。
# 这些文章必须交给 wechat 适配器，这里看到就直接让路（2026-10-17 陈少定）。
WECHAT_EXCLUDE = ("js_content", "activity-name", "js_name", "publish_time")

BASE_HREF_RE = re.compile(r'<base\s+href="([^"]+)"', re.I)
AUTHOR_RE = re.compile(r'class="author[^"]*"[^>]*>.*?itemprop="name"[^>]*>\s*([^<]+)<', re.S)
TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.I)
PUBLISHED_RE = re.compile(r'property="article:published_time"[^>]*content="([^"]+)"', re.I)
OG_TITLE_RE = re.compile(r'og:title"\s+content="([^"]*)"', re.I)

# 文件名非法字符；`%` 必须换成全角（与 wechat.py 一致，libxml2 会把路径当 URI）
_BAD = re.compile(r'[\\/:*?"<>|]+')


def _safe_stem(name: str) -> str:
    return _BAD.sub("_", name).replace("%", "％").strip(" .") or "未命名"


def _head_text(path: Path, limit: int = 1_000_000) -> str:
    """只读前面一段——元数据都在 head + 正文开头，无需读整个文件。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except Exception:
        return ""
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc, errors="ignore")
        except Exception:
            continue
    return ""


def _date_to_chinese(iso: str) -> str:
    """把 ISO 日期（2022-10-26 或 2022-10-26T00:00:00+08:00）转「2022年10月26日」。"""
    iso = (iso or "").strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", iso)
    if not m:
        return ""
    y, mo, d = m.group(1), str(int(m.group(2))), str(int(m.group(3)))
    return f"{y}年{mo}月{d}日"


class WebArchiveAdapter:
    name = "webarchive"

    def identify(self, path: Path) -> bool:
        p = Path(path)
        if p.suffix.lower() not in (".html", ".htm", ".xhtml"):
            return False
        text = _head_text(p)
        # 微信存档（base href 指向 mp.weixin.qq.com）让路给 wechat 适配器。
        if any(w in text for w in WECHAT_EXCLUDE):
            return False
        return any(m in text for m in MARKS)

    def extract(self, path: Path) -> RawDoc:
        p = Path(path)
        text = _head_text(p)
        if not text:
            return RawDoc(source_path=str(p), kind=self.name)

        # 标题：og:title 优先，其次 <title>（去掉「- 站点名」后缀）
        title = ""
        m = OG_TITLE_RE.search(text)
        if m:
            title = html_mod.unescape(m.group(1)).strip()
        if not title:
            m = re.search(r"<title>([^<]+)</title>", text)
            if m:
                raw = html_mod.unescape(m.group(1)).strip()
                # 「真抓到"大鱼"了 - 猫笔刀文章备份」→ 取「-」前的部分
                title = re.split(r"\s[-–|｜]\s", raw)[0].strip()

        # 日期：article:published_time 优先，其次 <time datetime>
        date = ""
        m = PUBLISHED_RE.search(text) or TIME_RE.search(text)
        if m:
            date = _date_to_chinese(m.group(1))

        # 链接：base href
        link = ""
        m = BASE_HREF_RE.search(text)
        if m:
            link = html_mod.unescape(m.group(1)).strip()

        # 作者：author vcard 里的 itemprop=name
        author = ""
        m = AUTHOR_RE.search(text)
        if m:
            author = html_mod.unescape(m.group(1)).strip()

        return RawDoc(
            title=title, date=date, author=author, account="",
            source_path=str(p), kind=self.name,
            meta={"link": link},
        )

    def render(self, doc: RawDoc, out_dir: Path) -> Path:
        """渲染成 Sigil XHTML。正文转换交给 sigi_convert（规则不在这里复制）。"""
        import sigi_convert

        src = Path(doc.source_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / ".tmp-convert-Sigil.xhtml"
        sigi_convert.convert(src, tmp, process_type="webarchive")

        # 回读产物 h1，格式形如「2022年10月26日_原标题」
        h1_title, h1_date = "", ""
        try:
            from lxml import etree
            tree = etree.parse(str(tmp))
            body = tree.getroot().find("{http://www.w3.org/1999/xhtml}body")
            if body is not None:
                for el in body.iter():
                    tag = str(el.tag)
                    if tag.endswith("}h1") or tag == "h1":
                        h1_title = "".join(el.itertext()).strip()
                        break
        except Exception:
            pass
        if h1_title:
            m = re.match(r"^(\d{4}年\d{1,2}月\d{1,2}日)[_\s]*(.*)$", h1_title)
            if m:
                h1_date, h1_title = m.group(1), m.group(2)

        date = h1_date or doc.date
        title = h1_title or doc.title or "无题"
        # 号名回退：网页存档没有 js_name，交给业务里的成熟识别（作者做号名兜底）
        account = doc.account
        if not account and doc.author:
            account = doc.author
        parts = [date, account or "未识别号", doc.author, title]
        stem = _safe_stem("_".join(x for x in parts if x))
        final = out_dir / f"{stem}-Sigil.xhtml"
        tmp.rename(final)
        return final


register(WebArchiveAdapter())
