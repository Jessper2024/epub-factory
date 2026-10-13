#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量 md → xhtml 迁移：把早期按 md 归档的文章统一到 xhtml + epub。

    ./epub.sh migrate-md            预演
    ./epub.sh migrate-md --apply    执行

背景（陈少 2026-10-13 定）
    早期知乎 / 小红书是按 `md/` 归档的，图片指向 `assets/` 外链；
    公众号那套是 `xhtml/` + 图片内嵌。两种格式并存会让工具链分叉，
    所以统一到 xhtml —— 图片全部内嵌，Sigil 里直接可用。

处理方式
    读 md → 转成 XHTML → 图片转 base64 内嵌 →
    写 `<作者>/xhtml/` → 原 md 与 assets 移入废纸篓（可恢复）。
"""

from __future__ import annotations

import argparse
import base64
import html as html_mod
import mimetypes
import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))

import platforms as P                            # noqa: E402
from build_epub import move_to_trash, safe_stem  # noqa: E402

IMG_RE = re.compile(r'<img[^>]*src="([^"]+)"[^>]*/?>', re.I)
H1_RE = re.compile(r"^#\s+(.+)$", re.M)
# md 的 h1 里带平台后缀：「阿里白兔姐 - 小红书」「知乎 - 标题」
PLATFORM_SUFFIX_RE = re.compile(
    r"\s*[-_|｜]\s*(小红书|知乎|微博|Twitter|推特|Notion|微信公众号|B站|油管)\s*$", re.I)
# 文件名形态：日期_作者_-_标题（也兼容 日期_平台_-_标题）
NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(.+?)_?-_(.+)$")


def strip_platform(value: str) -> str:
    """去掉标题里的平台后缀，反复去（有的会叠两个）。"""
    out = (value or "").strip()
    for _ in range(3):
        new = PLATFORM_SUFFIX_RE.sub("", out).strip()
        if new == out:
            break
        out = new
    return out.strip("_-— ").strip()


def embed_image(md_dir: Path, src: str) -> str:
    """把本地图片转成 base64 data URI。找不到返回空（调用方跳过该图）。"""
    rel = src.strip()
    for c in (md_dir / rel, md_dir.parent / rel, md_dir.parent.parent / rel):
        if c.is_file():
            mime = mimetypes.guess_type(c.name)[0] or "image/jpeg"
            return "data:%s;base64,%s" % (mime, base64.b64encode(c.read_bytes()).decode())
    return ""


def pick_title(md_path: Path, text: str) -> str:
    """定标题。

    优先级：文件名末段（`日期_作者_-_标题` 里的标题）→ h1 去后缀 → 正文首句。
    小红书那类源文件本来就没有标题（只有正文 + 话题标签），
    文件名末段会退化成平台名，这时必须从正文里取，否则标题就是「小红书」。
    """
    from_name = ""
    nm = NAME_RE.match(md_path.stem)
    if nm:
        from_name = strip_platform(nm.group(5))
    m = H1_RE.search(text)
    from_h1 = strip_platform(m.group(1)) if m else ""

    title = from_name or from_h1
    # 标题等于平台名 / 等于作者名 → 说明源文件没标题，从正文取首句
    if not title or _is_platform_or_author(title, md_path):
        body = text[m.end():] if m else text
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith(("<", "原文链接", "#")):
                continue
            # 去掉话题标签尾巴
            line = re.sub(r"\s*#[^#\s]+(\s*#[^#\s]+)*\s*$", "", line).strip()
            if not line:
                continue
            # 截到第一个句末标点：拿整段当标题会又长又难看
            cut = re.split(r"[。！？!?；;]", line)[0]
            return (cut or line)[:30]
    return (title or md_path.stem).strip()


def _is_platform_or_author(value: str, md_path: Path) -> bool:
    """这个"标题"是不是其实只是平台名或作者名。"""
    v = value.strip()
    if not v:
        return True
    if PLATFORM_SUFFIX_RE.fullmatch(" - " + v):
        return True
    platforms = ("小红书", "知乎", "微博", "Twitter", "推特", "Notion",
                 "微信公众号", "B站", "油管")
    if v in platforms:
        return True
    nm = NAME_RE.match(md_path.stem)
    if nm and nm.group(4) in (v, v.rstrip("_- ")):
        return True
    return False


def md_to_xhtml(md_path: Path, dest: Path, author: str) -> tuple[bool, int]:
    """一篇 md → xhtml。返回 (是否成功, 内嵌图片数)。"""
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    title = pick_title(md_path, text)

    m = H1_RE.search(text)
    body_lines = text[m.end():] if m else text

    embedded = 0

    def _replace(match):
        nonlocal embedded
        uri = embed_image(md_path.parent, match.group(1))
        if not uri:
            return ""                      # 图丢了就整块去掉，不留坏 img
        embedded += 1
        return '<p><img alt="" src="%s"/></p>' % uri

    body_html = IMG_RE.sub(_replace, body_lines)

    paras = []
    for block in re.split(r"\n\s*\n", body_html):
        block = block.strip()
        if not block or re.match(r"^\s*原文链接\s*[:：]", block):
            continue
        if block.startswith("<p><img"):
            paras.append(block)
        else:
            paras.append("<p>%s</p>" % html_mod.escape(block))

    ns = "http://www.w3.org/1999/xhtml"
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="%s"><head><meta http-equiv="Content-Type" '
        'content="application/xhtml+xml; charset=UTF-8"/><title>%s</title></head>'
        '<body><h1>%s</h1>\n<p class="article-meta">%s</p>\n%s\n</body></html>\n'
        % (ns, html_mod.escape(title), html_mod.escape(title),
           html_mod.escape(author), "\n".join(paras))
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(doc, encoding="utf-8")
    return True, embedded


def main() -> int:
    ap = argparse.ArgumentParser(description="存量 md → xhtml 迁移")
    ap.add_argument("--apply", action="store_true", help="真执行（默认只预演）")
    args = ap.parse_args()

    print("存量 md 迁移%s" % ("（执行）" if args.apply else "（预演）"))
    total = 0
    for _key, pname in P.PLATFORMS.items():
        pdir = P.LIB_ROOT / pname
        if not pdir.is_dir():
            continue
        for author_dir in sorted(d for d in pdir.iterdir() if d.is_dir()):
            md_dir = author_dir / "md"
            if not md_dir.is_dir():
                continue
            mds = sorted(md_dir.glob("*.md"))
            if not mds:
                continue
            print("\n【%s/%s】%d 篇 md" % (pname, author_dir.name, len(mds)))
            xdir = author_dir / "xhtml"
            for md in mds:
                # 统一命名：日期_作者_标题-Sigil.xhtml（跟公众号线一致）。
                # 标题走 pick_title（文件名末段 / h1 / 正文首句三级回退），
                # 否则小红书那类"没有标题"的文章会产出「日期_周不渡_小红书」。
                text = md.read_text(encoding="utf-8", errors="ignore")
                title = pick_title(md, text)
                stem = md.stem
                nm = NAME_RE.match(stem)
                if nm:
                    y, mo, d = nm.group(1), nm.group(2), nm.group(3)
                    date_cn = "%s年%d月%d日" % (y, int(mo), int(d))
                    stem = "_".join(x for x in (date_cn, author_dir.name, title) if x)
                dest = xdir / (safe_stem(stem) + "-Sigil.xhtml")
                if dest.exists():
                    print("  ⊘ 已有同名 xhtml，跳过：%s" % dest.name)
                    continue
                if not args.apply:
                    print("  [预演] %s\n         → xhtml/%s" % (md.name[:46], dest.name[:52]))
                    total += 1
                    continue
                ok, n_img = md_to_xhtml(md, dest, author_dir.name)
                if ok:
                    print("  ✓ %s（内嵌图 %d 张）" % (dest.name[:48], n_img))
                    total += 1
                else:
                    print("  ✗ 转换失败：%s" % md.name)
            if args.apply and mds:
                # md 与解出的图片都进废纸篓（可恢复），不留两套格式
                assets = author_dir / "assets"
                if assets.is_dir():
                    move_to_trash(assets)
                    print("  → assets/ 已移入废纸篓")
                move_to_trash(md_dir)
                print("  → md/ 已移入废纸篓")

    print("\n合计：%d 篇" % total)
    if not args.apply:
        print("（预演，未落地。确认后加 --apply）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
