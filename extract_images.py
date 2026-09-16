#!/usr/bin/env python3
"""从 SingleFile 存档 HTML 中解出全部内嵌 base64 图片，并按用途分类落盘。

用法：
    python3 extract_images.py <html 文件或目录> [<html 文件或目录> ...] [-o 输出根]

输出结构（默认输出根 ~/Life/EPUB制作/_图片解析）：
    <输出根>/<篇名>/正文配图/NN_宽x高.ext     # 来自 data-src 的正文插图，按文中出现顺序
    <输出根>/<篇名>/其他/…                     # 封面 / 头像 / 水印 / UI 装饰
    <输出根>/<篇名>/图片清单.json
    <输出根>/index.html                        # 全部篇目的总预览（点图可看原图）

分类判据是图片 base64 之前 500 字符的上下文（微信把 data-src 等塞在 JS 变量里，
属性名比 DOM 位置可靠）。同一张图在文件里出现多次只存一份（按 md5 去重）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_mod
import json
import os
import re
import struct
import sys

DATA_URI = re.compile(r"data:image/([a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=]+)")
CTX = 500
EXT = {"jpeg": "jpg", "svg+xml": "svg"}


def image_size(raw: bytes, fmt: str):
    """读文件头拿宽高，读不出来返回 (None, None)。"""
    f = fmt.lower().replace("jpeg", "jpg")
    try:
        if f == "png" and raw[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", raw[16:24])
        if f == "gif" and raw[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", raw[6:10])
        if f == "jpg":
            i = 2
            sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                   0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
            while i < len(raw) - 9:
                if raw[i] != 0xFF:
                    i += 1
                    continue
                mk = raw[i + 1]
                if mk in sof:
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return w, h
                if mk in (0xD8, 0xD9) or 0xD0 <= mk <= 0xD7:
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    except Exception:
        pass
    return None, None


def classify(ctx: str) -> str:
    """按上下文判断图片用途。返回 (类别, 文件名主体)。"""
    if "data-src" in ctx:
        return "正文配图"
    if "og:image" in ctx:
        return "封面"
    if "round_head_img" in ctx:
        return "头像"
    if "cdn_url_1_1" in ctx:
        return "封面方图"
    if "watermark_info" in ctx:
        return "水印素材"
    return "UI装饰"


def safe(s: str) -> str:
    return re.sub(r'[/\x00]', "_", s).strip() or "untitled"


def extract_one(path: str, out_root: str) -> dict:
    text = open(path, encoding="utf-8", errors="ignore").read()
    stem = safe(os.path.splitext(os.path.basename(path))[0])
    dest = os.path.join(out_root, stem)
    seen, rows = set(), []
    body_no = 0

    for m in DATA_URI.finditer(text):
        fmt, b64 = m.group(1), m.group(2)
        try:
            raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
        except Exception:
            continue
        digest = hashlib.md5(raw).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)

        ctx = text[max(0, m.start() - CTX):m.start()]
        w, h = image_size(raw, fmt)
        kind = classify(ctx)
        ext = EXT.get(fmt, fmt)

        if kind == "正文配图":
            body_no += 1
            name = f"{body_no:02d}_{w or 0}x{h or 0}.{ext}"
        else:
            name = f"{kind}_{digest[:6]}.{ext}"

        folder = os.path.join(dest, "正文配图" if kind == "正文配图" else "其他")
        os.makedirs(folder, exist_ok=True)
        rel = f"{'正文配图' if kind == '正文配图' else '其他'}/{name}"
        with open(os.path.join(dest, rel), "wb") as fh:
            fh.write(raw)
        rows.append(dict(kind=kind, file=rel, fmt=fmt, bytes=len(raw),
                         w=w, h=h, md5=digest[:8]))

    meta = dict(源=os.path.basename(path), 唯一图片=len(rows),
                正文配图=sum(1 for r in rows if r["kind"] == "正文配图"),
                images=rows)
    if rows:
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "图片清单.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1)
    return meta


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--line:#e3e6ea;--tx:#1b1f24;--tx2:#6b7280}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
 font:14px/1.6 -apple-system,"PingFang SC",sans-serif;padding:28px 32px 60px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--tx2);margin:0 0 20px}
.art{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:16px 18px;margin-bottom:18px}
.art h2{font-size:15px;margin:0 0 2px}
.art .m{color:var(--tx2);font-size:12px;margin:0 0 12px}
h3{font-size:13px;margin:14px 0 8px;color:var(--tx2);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.card{border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}
.card img{width:100%;display:block;background:#eceff3}
.card.sm img{max-height:150px;object-fit:contain}
.meta{padding:7px 9px;border-top:1px solid var(--line);
 display:flex;flex-direction:column;gap:2px}
.meta b{font-size:12px;word-break:break-all}
.meta span{color:var(--tx2);font-size:11.5px}
"""


def kb(n: int) -> str:
    return f"{n/1024:.0f} KB" if n < 1024 * 1024 else f"{n/1048576:.2f} MB"


def write_index(metas: list, out_root: str):
    def card(item, small):
        f = html_mod.escape(f"{item['_stem']}/{item['file']}")
        cls = "card sm" if small else "card"
        return (f'<div class="{cls}"><a href="{f}" target="_blank">'
                f'<img loading="lazy" src="{f}"></a><div class="meta">'
                f"<b>{html_mod.escape(item['file'].rsplit('/', 1)[-1])}</b>"
                f"<span>{item['w'] or '?'}×{item['h'] or '?'} · {item['fmt']} "
                f"· {kb(item['bytes'])}</span></div></div>")

    blocks = []
    for m in metas:
        if not m["images"]:
            continue
        body = [i for i in m["images"] if i["kind"] == "正文配图"]
        rest = [i for i in m["images"] if i["kind"] != "正文配图"]
        for i in m["images"]:
            i["_stem"] = m["_stem"]
        inner = ""
        if body:
            inner += f"<h3>正文配图（{len(body)}）</h3><div class='grid'>" + \
                "".join(card(i, False) for i in body) + "</div>"
        if rest:
            inner += f"<h3>封面 / 头像 / 水印 / 装饰（{len(rest)}）</h3><div class='grid'>" + \
                "".join(card(i, True) for i in rest) + "</div>"
        blocks.append(
            f"<section class='art'><h2>{html_mod.escape(m['源'])}</h2>"
            f"<p class='m'>唯一图片 {m['唯一图片']} 张 · 正文配图 {m['正文配图']} 张</p>"
            f"{inner}</section>")

    total = sum(m["唯一图片"] for m in metas)
    body_total = sum(m["正文配图"] for m in metas)
    page = ("<!doctype html><meta charset='utf-8'><title>图片解析总览</title>"
            f"<style>{CSS}</style><h1>图片解析总览</h1>"
            f"<p class='sub'>{len(metas)} 篇 · 共 {total} 张唯一图片 · 正文配图 {body_total} 张"
            f"</p>" + "".join(blocks))
    with open(os.path.join(out_root, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-o", "--out", default=os.path.expanduser("~/Life/EPUB制作/_图片解析"))
    ap.add_argument("-n", "--newest", type=int, default=0,
                    help="只处理修改时间最新的 N 个文件（传目录时用）")
    args = ap.parse_args()

    files = []
    for p in args.paths:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p))
                      if f.lower().endswith((".html", ".htm"))]
        else:
            files.append(p)

    if args.newest:
        files.sort(key=os.path.getmtime, reverse=True)
        files = files[:args.newest]

    os.makedirs(args.out, exist_ok=True)
    metas = []
    for f in files:
        m = extract_one(f, args.out)
        m["_stem"] = safe(os.path.splitext(os.path.basename(f))[0])
        metas.append(m)
        print(f"{m['源']}: 唯一 {m['唯一图片']} 张，正文配图 {m['正文配图']} 张")

    write_index(metas, args.out)
    print(f"\n输出根：{args.out}\n总览：{os.path.join(args.out, 'index.html')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
