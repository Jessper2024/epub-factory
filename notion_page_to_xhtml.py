#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SingleFile 保存的 Notion 页面 → 规范 XHTML（供 Sigil 导入 / EPUB 合订）。

专门处理「网页 → 另存 SingleFile」得到的 Notion 页面（notion-page-content 块结构），
与微信 js_content 是两套结构，故单独成器。

提取规则（2026-09-27 实测「明末四案06」定稿）：
  - 标题：<title> 去掉「 | Notion」后缀（或 <h1> 的 aria-roledescription=页面）
  - 正文：notion-page-content 容器内，按 data-block 的 class 识别块类型：
      notion-sub_header-block      → <h3>（小标题）
      notion-text-block            → <p>（正文段落）
      notion-quote-block           → <blockquote>
      notion-bulleted_list_item    → <li>（包进 <ul>）
      notion-numbered_list_item    → <li>（包进 <ol>）
      notion-image-block / <img>   → 图片（data-src 补 src；mmbiz/notion 图要 Referer）
      notion-callout-block         → 保留文本作 <p class="callout">
      notion-divider-block         → <hr>
  - 粉标 <span …color=pink>[待确认：…]</span> 原样保留
  - 清掉：script/style/编辑 UI 噪音/空块

用法：
  python3 notion_page_to_xhtml.py 单篇.html [-o 输出目录] [--title 覆盖标题]
"""
import argparse
import re
import sys
from pathlib import Path

from lxml import html as LH
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import emoji_tag  # noqa: E402

XHTML_NS = "http://www.w3.org/1999/xhtml"

# 删除的噪音标签
REMOVE_TAGS = {"button", "canvas", "form", "iframe", "input", "link", "meta",
               "script", "select", "style", "svg", "template", "textarea",
               "aside", "footer", "header", "nav", "noscript"}


def clean_title(raw: str) -> str:
    """<title> 或 <h1> 里的标题，去掉「| Notion」「 - Notion」等后缀。"""
    t = re.sub(r"\s*[|｜]\s*Notion\s*$", "", raw)
    t = re.sub(r"\s*-\s*Notion\s*$", "", t)
    return t.strip()


def block_text(block) -> str:
    """取一个 Notion 块内的纯文本（保留粉标文本，但先把 span 还原成 [待确认：…]）。"""
    # 直接拿块的 text_content，粉标已经在文本里
    return (block.text_content() or "").strip()


def extract(root: LH.HtmlElement) -> tuple[str, list]:
    """返回 (标题, [ (type, text) ... ])，type ∈ h3/p/quote/li/img/hr/callout。"""
    # 标题
    title = ""
    t = root.find(".//title")
    if t is not None and t.text:
        title = clean_title(t.text.strip())
    if not title:
        h1 = root.xpath("//h1[contains(@class,'content-editable-leaf')]")
        if h1:
            title = clean_title((h1[0].text_content() or "").strip())

    # 正文块：XPath 直接找 notion-selectable + -block + data-block-id
    # ⚠️ Notion 块 class 用**连字符**：notion-text-block / notion-sub_header-block / notion-image-block
    blocks = []
    for blk in root.xpath("//div[contains(@class,'notion-selectable') and contains(@class,'-block') and @data-block-id]"):
        cls = blk.get("class") or ""
        if "sub_header-block" in cls:
            blocks.append(("h3", block_text(blk)))
        elif "quote-block" in cls:
            blocks.append(("quote", block_text(blk)))
        elif "bulleted_list_item-block" in cls:
            blocks.append(("li", block_text(blk)))
        elif "numbered_list_item-block" in cls:
            blocks.append(("oli", block_text(blk)))
        elif "callout-block" in cls:
            blocks.append(("callout", block_text(blk)))
        elif "divider-block" in cls:
            blocks.append(("hr", ""))
        elif "image-block" in cls:
            img = blk.find(".//img")
            if img is not None:
                src = img.get("src") or img.get("data-src") or ""
                blocks.append(("img", src))
        elif "text-block" in cls or "header-block" in cls:
            blocks.append(("p", block_text(blk)))
        # notion-page-block 是页面元信息（标题/系列/日期/链接），跳过
    return title, blocks


def build_xhtml(title: str, blocks: list) -> str:
    E = lambda tag: etree.Element(f"{{{XHTML_NS}}}{tag}")  # noqa: E731
    root = E("div")
    root.set("class", "article")
    h = E("h1")
    h.text = title or "（无标题）"
    root.append(h)
    # 列表合并：连续 li 包进 ul，连续 oli 包进 ol
    i = 0
    while i < len(blocks):
        typ, text = blocks[i]
        if typ == "h3":
            e = E("h3"); e.text = text; root.append(e)
        elif typ == "quote":
            e = E("blockquote"); e.text = text; root.append(e)
        elif typ == "img":
            e = E("img"); e.set("src", text); root.append(e)
        elif typ == "hr":
            root.append(E("hr"))
        elif typ == "callout":
            e = E("p"); e.set("class", "callout"); e.text = text; root.append(e)
        elif typ in ("li", "oli"):
            ul = E("ul") if typ == "li" else E("ol")
            while i < len(blocks) and blocks[i][0] == typ:
                li = E("li"); li.text = blocks[i][1]; ul.append(li); i += 1
            root.append(ul)
            continue
        else:  # p
            e = E("p"); e.text = text; root.append(e)
        i += 1
    # 表情/贴图（长边 ≤256 的内嵌图）打 class="emoji"（陈少 2026-10-11）。
    # 注：本器不内联图片，仅对已是 data URI 的图生效；外链图由合订侧处理。
    emoji_tag.tag_small_images(root)
    return etree.tostring(root, encoding="unicode", pretty_print=True)


def main():
    ap = argparse.ArgumentParser(description="SingleFile Notion 页面 → XHTML")
    ap.add_argument("src")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    src = Path(args.src).expanduser()
    if not src.is_file():
        sys.exit(f"找不到文件：{src}")

    raw = src.read_text(encoding="utf-8", errors="replace")
    root = LH.fromstring(raw)
    title, blocks = extract(root)
    if args.title:
        title = args.title

    # 粉标：把 <span …color=pink>[待确认…]</span> 还原成纯文本 [待确认：…]（XHTML 里不嵌套 span）
    # 但当前实现用 text_content 已拿纯文本，粉标已保留为文字；这里不再额外处理 span。

    xhtml = build_xhtml(title, blocks)
    outdir = Path(args.outdir).expanduser() if args.outdir else src.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = clean_title(src.stem)
    out = outdir / f"{stem}.xhtml"
    out.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + xhtml, encoding="utf-8")

    print(f"标题：{title}")
    print(f"块数：{len(blocks)}（h3 {sum(1 for b in blocks if b[0]=='h3')} / 段 {sum(1 for b in blocks if b[0]=='p')} / 图 {sum(1 for b in blocks if b[0]=='img')}）")
    print(f"输出：{out}")


if __name__ == "__main__":
    main()
