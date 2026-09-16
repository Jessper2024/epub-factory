#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信公众号来源适配器。

只负责"认出微信文章 → 抽出元数据 → 渲染成 XHTML"这三件事。
真正的正文清洗、懒加载图还原、繁简/排版规则都在 sigi_convert.py 里，
本文件只是把它接到统一协议上——**不复制它的规则**，
否则规则会有两份，改一处漏一处（这是历史事故，别重犯）。

对业务脚本的 import 一律写在函数内（延迟导入），保持 core 不被反向依赖。
"""

from __future__ import annotations

import datetime as dt
import html as html_mod
import re
from pathlib import Path

from ..pipeline import RawDoc, register

# 微信文章的特征：正文容器或图床域名。命中任一即认为这是微信快照。
MARKS = ('id="js_content"', "mmbiz.qpic.cn")

OG_TITLE_RE = re.compile(r'og:title"\s+content="([^"]*)"')
CT_RE = re.compile(r'var\s+ct\s*=\s*"?(\d{9,10})')
JS_NAME_RE = re.compile(r'id="js_name"[^>]*>\s*([^<]{0,60})')
JS_AUTHOR_RE = re.compile(r'id="js_author_name"[^>]*>\s*([^<]{0,60})')
MSG_TITLE_RE = re.compile(r"var\s+msg_title\s*=\s*'([^']*)'")

# 文件名非法字符；`%` 必须换成全角（libxml2 会把路径当 URI，读写不对称）
_BAD = re.compile(r'[\\/:*?"<>|]+')


def safe_stem(name: str) -> str:
    return _BAD.sub("_", name).replace("%", "％").strip(" .") or "未命名"


def _head_text(path: Path, limit: int = 512_000) -> str:
    """只读前面一段——元数据都在 head 里，没必要读整个文件（有的快照几十 MB）。"""
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


class WeChatAdapter:
    name = "wechat"

    def identify(self, path: Path) -> bool:
        p = Path(path)
        if p.suffix.lower() not in (".html", ".htm", ".xhtml"):
            return False
        text = _head_text(p)
        return any(m in text for m in MARKS)

    def extract(self, path: Path) -> RawDoc:
        p = Path(path)
        text = _head_text(p)
        if not text:
            return RawDoc(source_path=str(p), kind=self.name)

        m = OG_TITLE_RE.search(text)
        title = html_mod.unescape(m.group(1)).strip() if m else ""
        if not title:
            m2 = MSG_TITLE_RE.search(text)
            title = html_mod.unescape(m2.group(1)).strip() if m2 else ""

        date = ""
        m = CT_RE.search(text)
        if m:
            try:
                # 手动拼，不用 %-m ——那个是 glibc 专属，macOS 的 strftime 不认
                d = dt.datetime.fromtimestamp(int(m.group(1)))
                date = f"{d.year}年{d.month}月{d.day}日"
            except Exception:
                date = ""

        account = ""
        m = JS_NAME_RE.search(text)
        if m:
            account = html_mod.unescape(m.group(1)).strip()
        if not account:
            # 号名错了会建出错误的号目录，把一个号拆成两本书（历史事故：王阿三/王张三）。
            # 所以抽不到时回退到业务里那套成熟识别，而不是在这里复制一份规则——
            # 规则一旦有两份，改一处漏一处是迟早的事。
            try:
                import build_epub  # 延迟导入：避免 core 在模块级依赖业务脚本
                account = build_epub.detect_account_from_html(p) or ""
            except Exception:
                account = ""

        author = ""
        m = JS_AUTHOR_RE.search(text)
        if m:
            author = html_mod.unescape(m.group(1)).strip()

        return RawDoc(
            title=title, date=date, author=author, account=account,
            source_path=str(p), kind=self.name,
        )

    def render(self, doc: RawDoc, out_dir: Path) -> Path:
        """渲染成 Sigil XHTML。正文转换交给 sigi_convert（规则不在这里复制）。

        文件名里的日期以**产物 h1 为准**：sigi_convert 才知道日期该怎么定
        （有的快照有 var ct、有的只能从正文/meta 推），adapter 自己抽的日期常常为空，
        硬用会产出"未识别号_标题"这种缺日期的名字。
        """
        # 延迟导入：core 不能在模块级依赖业务脚本
        import sigi_convert

        src = Path(doc.source_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / ".tmp-convert-Sigil.xhtml"
        sigi_convert.convert(src, tmp, process_type="wechat")

        # 回读产物 h1，格式形如「2026年9月6日_原标题」
        h1_title, h1_date = "", ""
        try:
            from lxml import etree
            tree = etree.parse(str(tmp))
            body = tree.getroot().find("{http://www.w3.org/1999/xhtml}body")
            if body is not None:
                for el in body.iter():
                    if str(el.tag).endswith("}h1") or str(el.tag) == "h1":
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
        parts = [date, doc.account or "未识别号", doc.author, title]
        stem = safe_stem("_".join(x for x in parts if x))
        final = out_dir / f"{stem}-Sigil.xhtml"
        tmp.rename(final)
        return final


register(WeChatAdapter())
