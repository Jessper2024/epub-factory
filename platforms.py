#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台判定与落盘路由：一份 Notion 导出该进哪个平台目录、哪个作者目录。

陈少 2026-10-13 定：来源判定分两层。
    第一层  看属性表「来源」/「类型」字段的值
    第二层  看属性表「文章链接」的域名，**冲突时第二层优先**
            （链接是硬证据，字段可能填错）

平台目录（`02、定稿库/` 下，编号不可改）：
    01、微信公众号  02、知乎  03、小红书  04、微博
    05、Twitter    06、B站   07、油管   08、出版图书

为什么单独成模块而不是塞进 build_epub.py：
    判定规则会被多处用到（收文、体检、台账、迁移存量），
    塞进主脚本里又会变成"规则两份、改一处漏一处"。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB_ROOT = ROOT / "02、定稿库"

# 平台目录名（编号 + 名称）。编号是陈少的目录体系，别改。
PLATFORMS = {
    "wechat": "01、微信公众号",
    "zhihu": "02、知乎",
    "xiaohongshu": "03、小红书",
    "weibo": "04、微博",
    "twitter": "05、Twitter",
    "bilibili": "06、B站",
    "youtube": "07、油管",
    "book": "08、出版图书",
}

# 第一层：属性表「来源」/「类型」字段的值 → 平台
# 值可能带后缀（如「知乎 - 子不语999」），所以用「包含」而不是全等。
SOURCE_LABEL_PATTERNS = (
    ("wechat", ("微信", "公众号", "wechat")),
    ("zhihu", ("知乎", "zhihu")),
    ("xiaohongshu", ("小红书", "xiaohongshu", "xhs")),
    ("weibo", ("微博", "weibo")),
    ("twitter", ("推特", "twitter", "x.com")),
    ("bilibili", ("b站", "bilibili", "哔哩")),
    ("youtube", ("油管", "youtube", "优兔")),
)

# 第二层：原文链接域名 → 平台（硬证据，优先级最高）。
# 注意 `(?:^|\.)` 之前**不能**要求有内容——`https://x.com/abc` 里域名前面是 `//`，
# 用 `(?:^|/|\.)` 才匹配得到（2026-10-13 踩过：Twitter 链接因此认不出）。
DOMAIN_PATTERNS = (
    ("zhihu", (r"(?:^|[/.])zhihu\.com(?:[/?]|$)",)),
    ("xiaohongshu", (r"(?:^|[/.])xiaohongshu\.com(?:[/?]|$)", r"(?:^|[/.])xhslink\.com(?:[/?]|$)")),
    ("twitter", (r"(?:^|[/.])x\.com(?:[/?]|$)", r"(?:^|[/.])twitter\.com(?:[/?]|$)")),
    ("weibo", (r"(?:^|[/.])weibo\.(?:com|cn)(?:[/?]|$)",)),
    ("wechat", (r"(?:^|[/.])mp\.weixin\.qq\.com(?:[/?]|$)",)),
    ("bilibili", (r"(?:^|[/.])bilibili\.com(?:[/?]|$)", r"(?:^|[/.])b23\.tv(?:[/?]|$)")),
    ("youtube", (r"(?:^|[/.])youtube\.com(?:[/?]|$)", r"(?:^|[/.])youtu\.be(?:[/?]|$)")),
)


def platform_from_source_label(value: str) -> str:
    """第一层：属性表「来源」/「类型」的值 → 平台 key（认不出返回空）。"""

    text = (value or "").strip().casefold()
    if not text:
        return ""
    for key, needles in SOURCE_LABEL_PATTERNS:
        if any(n in text for n in needles):
            return key
    return ""


def platform_from_url(url: str) -> str:
    """第二层：原文链接 → 平台 key（认不出返回空）。"""

    text = (url or "").strip()
    if not text:
        return ""
    for key, patterns in DOMAIN_PATTERNS:
        if any(re.search(p, text, re.I) for p in patterns):
            return key
    return ""


def resolve_platform(source_label: str = "", url: str = "") -> tuple[str, str]:
    """两层判定合起来。返回 (平台 key, 判定依据说明)。

    第二层优先：链接域名是硬证据，属性表的「来源」字段可能填错或留空。
    第一层兜底：有字段但没链接的文章靠它归类。
    都判不出返回 ("", 原因)——**不猜**，让调用方决定是搁置还是问人。
    """

    by_url = platform_from_url(url)
    if by_url:
        by_source = platform_from_source_label(source_label)
        if by_source and by_source != by_url:
            return by_url, "链接域名=%s（属性表写的是 %s，以链接为准）" % (by_url, by_source)
        return by_url, "链接域名"
    by_source = platform_from_source_label(source_label)
    if by_source:
        return by_source, "属性表来源=%s（无原文链接）" % source_label.strip()
    return "", "属性表来源=%r、链接=%r 都认不出" % (source_label, url)


def platform_dir(key: str) -> Path:
    """平台 key → 定稿库下的目录（不存在则创建）。"""

    name = PLATFORMS.get(key)
    if not name:
        raise ValueError("未知平台：%r（可选：%s）" % (key, "、".join(PLATFORMS)))
    path = LIB_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def book_dir(key: str, author: str) -> Path:
    """平台 + 作者 → 作者目录（不存在则创建）。

    目录结构：`02、定稿库/<平台>/<作者>/`，各平台天然隔离
    （同一作者在知乎和小红书是两本，这是陈少要的）。
    """

    from build_epub import safe_stem      # 延迟导入：避免模块级互相依赖

    clean = safe_stem(author) if author else "未识别作者"
    path = platform_dir(key) / clean
    path.mkdir(parents=True, exist_ok=True)
    return path


def scan_platform_dirs() -> dict:
    """现有平台目录一览，供体检与报表用。"""

    out: dict = {}
    for key, name in PLATFORMS.items():
        d = LIB_ROOT / name
        authors = []
        if d.is_dir():
            authors = sorted(p.name for p in d.iterdir()
                             if p.is_dir() and not p.name.startswith((".", "_")))
        out[key] = {"dir": name, "exists": d.is_dir(), "authors": authors}
    return out
