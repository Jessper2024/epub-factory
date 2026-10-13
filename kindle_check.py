#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kindle 兼容性校验（陈少 2026-10-13 定，长期硬约束）。

保证生成的 EPUB 推送给 Kindle 时图片不报错、不缺失。Kindle 的 EPUB→KFX 转换：
  - 不支持 WebP / SVG / TIFF / BMP 等格式（直接不显示）；
  - 不支持内联 base64 `data:` 图（转换会丢弃）；
  - 抓不到外链 `http(s):` 图；
  - 引用了但包里没有的图片 = 裂图。

所以一张「Kindle 友好」的图必须：外部资源（OEBPS/Images/ 下）+ JPEG/PNG/GIF 格式。
本模块的 `check_epub_kindle(path)` 返回问题清单（空 = 友好），被两处调用：
  1. build_epub.py 出书后自动跑（不达标 → 醒目标红告警）；
  2. check_epub.py --kindle / 默认体检跑（人工复核）。
"""
import zipfile
from pathlib import Path

from lxml import etree

# Kindle 支持的静态图片扩展名（不带点，与 rsplit 取出的一致）
KINDLE_IMG_EXT = {"jpg", "jpeg", "png", "gif"}


def check_epub_kindle(epub_path) -> list[str]:
    """返回 Kindle 兼容性问题列表；空列表 = 友好。"""
    issues: list[str] = []
    try:
        z = zipfile.ZipFile(epub_path)
    except Exception as e:  # noqa: BLE001
        return ["EPUB 打不开: %s" % e]

    names = set(z.namelist())

    # 1) 包内所有图片文件必须是 Kindle 支持的格式
    for n in sorted(names):
        if not n.startswith("OEBPS/Images/"):
            continue
        ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
        if ext not in KINDLE_IMG_EXT:
            issues.append("Kindle 不支持的图片格式: %s" % n)

    # 2) 文本里的 <img> 引用：不允许 data: / http(s):，且相对引用必须存在
    for n in sorted(names):
        if not (n.startswith("OEBPS/Text/") and n.endswith(".xhtml")):
            continue
        try:
            root = etree.fromstring(z.read(n))
        except Exception:  # noqa: BLE001
            continue
        for el in root.iter():
            tag = el.tag
            if not (isinstance(tag, str) and tag.split("}")[-1] == "img"):
                continue
            src = el.get("src") or ""
            if src.startswith("data:"):
                issues.append("%s 仍有内联 data: 图（Kindle 会丢）" % n)
            elif src.startswith(("http://", "https://")):
                issues.append("%s 仍有外链图 %s（Kindle 抓不到）" % (n, src[:40]))
            elif src.startswith("../Images/") or src.startswith("Images/"):
                target = "OEBPS/" + (src[3:] if src.startswith("../") else src)
                if target not in names:
                    issues.append("%s 引用图片缺失: %s" % (n, src))
            else:
                issues.append("%s 图片引用异常: %s" % (n, src[:40]))

    z.close()
    return issues


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        iss = check_epub_kindle(p)
        if iss:
            print(p, "✗")
            for i in iss:
                print("  ", i)
        else:
            print(p, "✓ Kindle 友好")
