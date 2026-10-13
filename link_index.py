#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""链接级判重索引：{原文链接的稳定 ID: "平台/作者/文件名"}。

为什么需要它（陈少 2026-10-13 定）
    标题判重在两种情况下会漏：
      1. 同一篇从不同来源拿到，标题写法不同
         （「阿里白兔姐」vs 从正文首句取的「你第一眼看见白兔姐…」）
      2. 老 md 迁移版与 Notion 原件标题不一致
    原文链接里的 ID 是稳定的——同一篇文章不论从哪来都指向同一个 URL，
    所以它是比标题硬得多的判重依据。

稳定 ID 怎么取
    zhihu        /question/639501710 或 /answer/123  → q639501710 / a123
    xiaohongshu  /explore/6a9e5d58000000002601ee12   → xhs6a9e5d58...
    x.com        /Morris_LT/status/1234567890        → x1234567890
    weibo        /1234/abcxyz                        → wbabcxyz
    wechat       mp.weixin.qq.com/s?...sn=xxxx        → 用 sn（跟指纹表同源）

落盘：`_链接索引.json`。入料时先查这里，命中就直接跳过并清原件。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_FILE = ROOT / "_链接索引.json"

# 各平台的稳定 ID 提取规则：(平台, 正则, 前缀)
ID_RULES = (
    ("zhihu", re.compile(r"/question/(\d+)"), "q"),
    ("zhihu", re.compile(r"/answer/(\d+)"), "a"),
    ("zhihu", re.compile(r"/p/(\d+)"), "p"),
    ("xiaohongshu", re.compile(r"/explore/([0-9a-f]+)"), "xhs"),
    ("xiaohongshu", re.compile(r"/discovery/item/([0-9a-f]+)"), "xhs"),
    ("twitter", re.compile(r"/status/(\d+)"), "x"),
    ("weibo", re.compile(r"weibo\.(?:com|cn)/\d+/([A-Za-z0-9]+)"), "wb"),
    ("wechat", re.compile(r"[?&]sn=([0-9a-f]{32})"), "sn"),
    ("bilibili", re.compile(r"/video/(BV[A-Za-z0-9]+)"), "bv"),
    ("youtube", re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})"), "yt"),
)


def link_id(url: str) -> str:
    """原文链接 → 稳定 ID（认不出返回空，**不猜**）。"""
    text = (url or "").strip()
    if not text:
        return ""
    for _platform, pattern, prefix in ID_RULES:
        m = pattern.search(text)
        if m:
            return prefix + m.group(1)
    return ""


def load() -> dict:
    """{稳定 ID: "平台/作者/文件名"}。坏了返回空表，不让索引问题挡住入料。"""
    try:
        if INDEX_FILE.exists():
            data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save(mapping: dict) -> None:
    try:
        INDEX_FILE.write_text(json.dumps(mapping, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except OSError:
        pass


def find(url: str) -> str:
    """这个链接收过没有。返回已有文件的描述，没收录返回空。"""
    lid = link_id(url)
    return load().get(lid, "") if lid else ""


def register(url: str, where: str) -> bool:
    """登记一篇的链接 ID。返回是否新增（用于统计）。"""
    lid = link_id(url)
    if not lid:
        return False
    idx = load()
    if lid in idx:
        return False
    idx[lid] = where
    save(idx)
    return True


def count() -> int:
    return len(load())
