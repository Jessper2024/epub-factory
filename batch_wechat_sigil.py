#!/usr/bin/env python3
"""批量调用 sigi_convert.py 把微信公众号 SingleFile 快照转成 Sigil XHTML。

命名沿用 日期_公众号_作者_标题-Sigil.xhtml，
与 /Users/jessper/Life/EPUB制作 下已有的成品保持一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sigi_convert as S  # noqa: E402
from lxml import etree, html as LH  # noqa: E402

SRC = Path("/Users/jessper/Downloads/微信公众号下载").expanduser()
OUT = Path("/Users/jessper/Life/EPUB制作/猫刀笔").expanduser()


def target_name(path: Path) -> str:
    source = LH.fromstring(path.read_bytes(), parser=LH.HTMLParser(encoding="utf-8"))
    title = S._article_title_from_source(source)
    if "_" in title:
        date_text, base = title.split("_", 1)
    else:
        date_text, base = "", title
    account = S._first_text_by_id(source, "js_name") or ""
    author = (
        S._first_text_by_id(source, "js_author_name")
        or S._author_from_source(source)
    )
    stem = "_".join(part for part in (date_text, account, author, base) if part)
    return S.output_stem_for_title(stem) + "-Sigil.xhtml"


def main() -> int:
    files = sorted(p for p in SRC.glob("*.html") if p.is_file())
    OUT.mkdir(parents=True, exist_ok=True)
    total_img = total_embedded = 0
    for path in files:
        try:
            out_path = OUT / target_name(path)
            paragraphs, removed, title = S.convert(path, out_path, "wechat", False)
            report = S._image_report(
                etree.parse(
                    str(out_path), etree.XMLParser(resolve_entities=False, no_network=True)
                ).getroot()
            )
            total_img += report["images"]
            total_embedded += report["embedded"]
            print(
                f"OK  {path.name[:24]:<26} → {out_path.name[:44]:<46} "
                f"段落 {paragraphs:>3}  图 {report['images']}（内嵌 {report['embedded']}，外链 {report['external']}）"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {path.name}: {type(exc).__name__}: {exc}")
    print(f"\n共 {len(files)} 篇；图片 {total_img} 张，内嵌 {total_embedded} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
