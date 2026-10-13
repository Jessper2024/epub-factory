#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小图（表情/贴图）标记：长边 ≤ EMOJI_MAX_PX 的内嵌图打 class="emoji"。

微信表情/贴图自然尺寸很小（常见 128×128），但各端 CSS 的 ``img{max-width:100%}``
只限制「不超页宽」，不会把它缩到与正文相称——阅读器就按原始像素铺开，
128px 的捂脸脸谱顶一整行，观感差。陈少 2026-10-11 定：**不删图，缩到 2em 内联**。

本模块是**唯一的判定与样式来源**，三处共用，避免逻辑漂移：
  - 转换器（sigi_convert / wechat_to_sigil_xhtml / notion_page_to_xhtml）
    在生成源 xhtml 时打标，并把 ``EMOJI_CSS_RULE`` 注入内联 <style>；
  - 批量回改脚本补标历史源 xhtml；
  - build_epub 打包时兜底（幂等）。
"""
from __future__ import annotations

import base64
import struct

EMOJI_MAX_PX = 256          # 长边 ≤ 此值当作表情/贴图（正文字号约 2em ≈ 32px）
EMOJI_CLASS = "emoji"

# 内联随文字：像真表情一样跟在句尾；大图（正文配图）不受影响，仍走 max-width:100%。
EMOJI_CSS_RULE = """img.emoji {
  display: inline;
  max-width: 2em;
  max-height: 2em;
  width: auto;
  height: auto;
  vertical-align: text-bottom;
  margin: 0 0.1em;
}"""


def _local_name(tag) -> str:
    """取标签本地名（去命名空间），HTML 解析与 XHTML 都能用。"""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def image_natural_size(data_uri: str) -> tuple[int, int]:
    """从 data URI 读图片自然尺寸（PNG / GIF / JPEG）。读不到返回 (0, 0)。"""
    try:
        b64 = data_uri.split(",", 1)[1]
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception:
        return (0, 0)
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        w, h = struct.unpack(">II", raw[16:24])
        return (w, h)
    if raw[:6] in (b"GIF87a", b"GIF89a") and len(raw) >= 10:
        w, h = struct.unpack("<HH", raw[6:10])
        return (w, h)
    if raw[:2] == b"\xff\xd8":                       # JPEG：扫 SOF 段取高宽
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                return (w, h)
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
        return (0, 0)
    return (0, 0)


def tag_small_images(roots, max_px: int = EMOJI_MAX_PX) -> int:
    """给自然尺寸 ≤ max_px 的 <img> 加 class="emoji"，返回**新加**标记的图数。

    roots 可以是单个元素，或元素列表；命名空间无关。**幂等**：已带 emoji 的跳过。
    只改内存里的树，不动文件（落盘由调用方决定）。
    """
    if roots is None:
        return 0
    elems = list(roots) if isinstance(roots, (list, tuple, set)) else [roots]
    n = 0
    # 不做基于 id() 的去重：lxml 元素是即时创建的代理对象，会被回收、id 会被复用，
    # 用 id() 判重会把后面的图误当成"已处理"跳过（2026-10-11 实测漏标）。
    # 单个树内 iter() 不会重复产出同一节点；跨元素列表重复访问时第二次也会因
    # 已带 emoji 而跳过，天然幂等。
    for root in elems:
        if not hasattr(root, "iter"):
            continue
        for node in root.iter():
            if _local_name(getattr(node, "tag", None)) != "img":
                continue
            src = node.get("src") or ""
            if not src.startswith("data:image"):
                continue
            w, h = image_natural_size(src)
            if 0 < max(w, h) <= max_px:
                classes = (node.get("class") or "").split()
                if EMOJI_CLASS not in classes:
                    node.set("class", " ".join(classes + [EMOJI_CLASS]))
                    n += 1
    return n


def ensure_emoji_css(css_text: str) -> tuple[str, bool]:
    """把 ``img.emoji`` 规则**幂等**注入 CSS 文本。返回 (新文本, 是否改动)。"""
    if "img.emoji" in css_text:
        return css_text, False
    sep = "" if (not css_text or css_text.endswith("\n")) else "\n"
    return css_text + sep + EMOJI_CSS_RULE + "\n", True


if __name__ == "__main__":      # 自测：python3 emoji_tag.py
    import io
    from lxml import etree
    ihdr = struct.pack(">II", 128, 128)
    b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr).decode()
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
                           + struct.pack(">II", 1280, 800)).decode()
    doc = etree.fromstring(
        f'<div><p>文字<img src="data:image/png;base64,{b64}"/></p>'
        f'<p><img src="data:image/png;base64,{big}"/></p></div>')
    print("natural:", image_natural_size("data:image/png;base64," + b64))
    print("tagged:", tag_small_images(doc))
    print("classes:", [e.get("class") for e in doc.iter() if _local_name(e.tag) == "img"])
    print("ensure_css:", ensure_emoji_css("img { max-width: 100%; }")[1],
          ensure_emoji_css(EMOJI_CSS_RULE)[1])
