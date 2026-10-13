#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量回改源 xhtml：给 ≤256 的内嵌小图补 class="emoji"，并把 img.emoji 规则注入内联 <style>。

为什么：新转换的 xhtml 已由转换器打好标（见 emoji_tag.py），但历史源文件还没有——
本脚本一次性补齐，之后 build 期的兜底就不会再兜到东西。

幂等：已带标记 / 已带规则的跳过。只加类属性与一条 CSS 规则，不动正文其他内容。
用法：
    python3 tag_emoji_sources.py --dry-run     # 预演，只报不改
    python3 tag_emoji_sources.py               # 正式回改（建议先备份）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_epub as B       # noqa: E402  借用 collect_book_dirs / source_dir
import emoji_tag             # noqa: E402

DOCTYPE = "<!DOCTYPE html>"


def _find_style(root):
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1].lower() == "style":
            return el
    return None


def process_file(p: Path, dry: bool) -> tuple[int, bool, bool]:
    """返回 (新加 emoji 图数, style 是否改动, 是否写盘)。"""
    try:
        tree = etree.parse(str(p), etree.XMLParser(resolve_entities=False, no_network=True))
    except Exception as exc:                       # noqa: BLE001
        print("  ✗ 解析失败，跳过：%s -> %s" % (p.name, exc))
        return (0, False, False)
    root = tree.getroot()
    n = emoji_tag.tag_small_images(root)
    style = _find_style(root)
    css_changed = False
    if style is not None:
        new_text, changed = emoji_tag.ensure_emoji_css(style.text or "")
        if changed:
            style.text = new_text
            css_changed = True
    wrote = False
    if (n or css_changed) and not dry:
        with open(p, "wb") as fh:
            tree.write(fh, encoding="UTF-8", xml_declaration=True,
                       doctype=DOCTYPE, pretty_print=False)
        wrote = True
    return (n, css_changed, wrote)


def main() -> int:
    ap = argparse.ArgumentParser(description="全量回改源 xhtml：补 class=emoji + 内联样式规则")
    ap.add_argument("--dry-run", action="store_true", help="只报不改，一个文件都不动")
    args = ap.parse_args()

    files = changed = tagged = css_n = 0
    for d in B.collect_book_dirs():
        xd = B.source_dir(d)
        for p in sorted(xd.glob("*.xhtml")):
            files += 1
            n, css_changed, _wrote = process_file(p, args.dry_run)
            if n or css_changed:
                changed += 1
                tagged += n
                if css_changed:
                    css_n += 1
                print("  %s  +%d emoji%s%s" % (
                    p.name, n, "  +style" if css_changed else "",
                    "  （预演）" if args.dry_run else ""))
    print("=" * 60)
    print("扫描 %d 个源 xhtml → 需改 %d 个；新加 emoji 标记 %d 处，注入 style 规则 %d 处%s"
          % (files, changed, tagged, css_n,
             "（预演，未落盘）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
