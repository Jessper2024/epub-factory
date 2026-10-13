#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notion 导出（微信来源）适配器。

为什么要有它
    公众号文章被作者删掉后，微信官网就抓不到了，只能从存过的 Notion 页面
    导出 HTML 来恢复。这跟微信直取的 HTML 是**两套完全不同的前端结构**，
    但陈少要的是：**在后端产出同一种 XHTML / EPUB，效果一模一样**。

    所以本适配器只做一件事——把 Notion 那套结构翻译成跟 WeChatAdapter
    完全相同的 RawDoc，然后调用**同一个**渲染入口。产出的一致性由
    sigi_convert 保证，本文件不复制任何排版规则。

怎么认出它
    Notion 导出特征：`notion-page-content` 容器 + 属性表。
    再要求属性表「来源」= 微信（陈少 2026-10-13 定的判定口径）——
    只认微信来源的，其它来源的 Notion 页面不走这条线。

字段映射（Notion 属性表 → 跟微信同名的字段）
    文章链接  → biz → 号名（优先）；拿不到退回「作者」属性
    发布时间  → 日期，输出成「2026年9月29日」
    作者      → 可见作者名（微信侧对应 js_author_name）
    来源      → 必须是「微信」，否则不接
    正文      → notion-page-content
"""

from __future__ import annotations

import re
from pathlib import Path

from ..pipeline import RawDoc, register

# Notion 导出的两种结构特征：
#   notion-page-content  SingleFile 存的 Notion 网页快照
#   page-body            Notion 官方 Export 出的静态 HTML
MARKS = ("notion-page-content", "page-body")

_SOURCE_LABEL = re.compile(r"来源", re.I)
_WECHAT_VALUE = re.compile(r"微信")


def _head_text(path: Path, limit: int = 2_000_000) -> str:
    """只读前面一段做初筛。Notion 导出通常几 MB，前 2MB 足够看到属性表。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except Exception:
        return ""
    return raw.decode("utf-8", errors="ignore")


def _select_source_node(source):
    """取正文容器，取不到返回 None。

    两种导出格式的容器名不同（notion-page-content / page-body），
    这里跟 sigi_convert 的选择口径保持一致，但**不复制正文清洗规则**。
    """
    for selector in (
        '//*[contains(concat(" ", normalize-space(@class), " "), " notion-page-content ")]',
        '//div[contains(@class, "page-body")]',
    ):
        nodes = source.xpath(selector)
        if nodes:
            return nodes[0]
    return None


class NotionWeChatAdapter:
    name = "notion_wechat"

    def identify(self, path: Path) -> bool:
        p = Path(path)
        if p.suffix.lower() not in (".html", ".htm", ".xhtml"):
            return False
        text = _head_text(p)
        if not any(m in text for m in MARKS):
            return False
        # 来源必须是「微信」。只判 Notion 结构不够——Notion 导出里
        # 还可能有别的平台的文章，那些不该走微信这条线。
        try:
            from lxml import html as LH
            source = LH.fromstring(p.read_bytes(),
                                   parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
        except Exception:
            return False
        return self._is_wechat_source(source)

    @staticmethod
    def _is_wechat_source(source) -> bool:
        """属性表里「来源」= 微信 才认。

        复刻 sigi_convert 的 cell 配对逻辑（属性名 + 属性值成对），
        但只读不看——不改写任何东西。
        """
        try:
            import sigi_convert as S
        except Exception:
            return False
        for source_label in ("来源", "source"):
            value = S._notion_property(source, source_label)
            if value:
                return bool(_WECHAT_VALUE.search(value))
        return False

    def extract(self, path: Path) -> RawDoc:
        p = Path(path)
        try:
            from lxml import html as LH
            source = LH.fromstring(p.read_bytes(),
                                   parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
        except Exception:
            return RawDoc(source_path=str(p), kind=self.name)

        import sigi_convert as S

        # 元数据全部从属性表读；读不到的留空，不猜。
        url = S.notion_source_url(source)
        date_raw = S._notion_property(source, "发布时间")
        date = S._notion_date(date_raw)
        author = S._notion_property(source, "作者").strip()
        account = S._notion_account_name(source) or author

        return RawDoc(
            title=S._base_title_from_source(source),
            date=date,
            author=author,
            account=account,
            source_path=str(p),
            kind=self.name,
            meta={"source_url": url, "biz": S.notion_biz(source)},
        )

    def render(self, doc: RawDoc, out_dir: Path) -> Path:
        """渲染成 XHTML —— 走 sigi_convert 的 notion 分支，产出格式跟微信完全一致。

        跟 WeChatAdapter.render 是同一套收尾逻辑（回读产物 h1 定日期、拼四段文件名），
        刻意分两处写而不是抽公共函数：两个 adapter 的文件名规则需要能**各自演进**，
        真要改了再抽也不迟。
        """
        import sigi_convert

        src = Path(doc.source_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / ".tmp-notion-convert-Sigil.xhtml"
        sigi_convert.convert(src, tmp, process_type="notion")

        # 回读产物 h1（格式「2026年9月29日_标题」）——日期以产物为准，
        # 因为 sigi_convert 才知道日期该怎么定，adapter 抽的可能是空的。
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
        stem = _safe_stem("_".join(x for x in parts if x))
        final = out_dir / f"{stem}-Sigil.xhtml"
        tmp.rename(final)
        return final


_BAD = re.compile(r'[\\/:*?"<>|]+')


def _safe_stem(name: str) -> str:
    """跟 wechat adapter 同一套：非法字符替换、`%` 换全角（libxml2 把路径当 URI）。"""
    return _BAD.sub("_", name).replace("%", "％").strip(" .") or "未命名"


register(NotionWeChatAdapter())
