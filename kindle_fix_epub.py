#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPUB 图片「阅读器友好」后处理（Kindle / Kobo 通用，陈少 2026-10-13 定）。

作用：对任意已生成的 EPUB 做一次就地重构，保证推到 Kindle/Kobo 不再丢图：
  1. 把所有内联 `data:` base64 图抽成 OEBPS/Images/ 外部资源（WebP/GIF→JPEG、PNG/JPEG 原样）；
  2. 把包内残留的 .webp 文件也转成 .jpg 并改写引用；
  3. 更新 OPF manifest，改写 xhtml 里 <img src>。
  4. 出书后自动跑 kindle_check，不达标则不替换原文件。

已满足「图片 = 外部 JPG/PNG 且无内联 / 无外链 / 无缺失」的书会被判定为已友好，直接跳过、不改动。
本脚本独立于 build_epub.py，可直接作用于任何 EPUB（文稿线 / 成人作品集线 / 写真集线），
作为「阅读器友好」能力的统一迁移工具。

用法：
  kindle_fix_epub.py <epub路径或目录> [--check-only] [--no-backup] [--force]
  - 传目录：递归处理其下所有 *.epub
  - --check-only：只跑 kindle_check 报状态，不改动
  - --force：即便判定已友好也重写一遍（一般不必）
"""
import sys
import os
import re
import io
import base64
import hashlib
import zipfile
import shutil
from pathlib import Path

from PIL import Image

try:
    from kindle_check import check_epub_kindle
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from kindle_check import check_epub_kindle

IMG_DIR = "OEBPS/Images"
DATA_URI_RE = re.compile(rb'(src|xlink:href)\s*=\s*(["\'])(data:image/[^"\']+)\2', re.I)
MANIFEST_CLOSE_RE = re.compile(rb'</manifest\s*>', re.I)
CONTAINER_RE = re.compile(rb'full-path="([^"]+)"')
IMG_TAG_RE = re.compile(rb'<img\b[^>]*>', re.I)


def decode_data_uri(uri: str):
    m = re.match(r"data:([^;]+);base64,(.*)$", uri, re.S)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None
    return m.group(1).lower(), raw


def process_image(mime: str, raw: bytes):
    """返回 (ext, bytes)。口径与 build_epub._data_uri_to_image 一致。"""
    ext = None
    out = None
    try:
        im = Image.open(io.BytesIO(raw))
        fmt = (im.format or "").upper()
    except Exception:
        im, fmt = None, ""
    if mime == "image/webp" or fmt == "WEBP":
        im = im.convert("RGBA") if im.mode in ("RGBA", "LA") else im.convert("RGB")
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
        out, ext = buf.getvalue(), "jpg"
    elif mime == "image/gif" or fmt == "GIF":
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        buf = io.BytesIO()
        bg.save(buf, "JPEG", quality=85)
        out, ext = buf.getvalue(), "jpg"
    elif mime == "image/png" or fmt == "PNG":
        out, ext = raw, "png"          # PNG Kindle 支持，原样保留
    else:
        # jpeg 或其它：原样（jpeg 当 jpg，其它兜底当 jpg）
        ext = "jpg" if mime == "image/jpeg" else "jpg"
        out = raw if mime == "image/jpeg" else raw
    return ext, out


def media_type(ext: str) -> str:
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif"}.get(ext, "image/jpeg")


def find_opf(z: zipfile.ZipFile) -> str | None:
    try:
        data = z.read("META-INF/container.xml")
    except KeyError:
        return None
    m = CONTAINER_RE.search(data)
    return m.group(1).decode("utf-8") if m else None


def rel_path(from_pkg: str, to_pkg: str) -> str:
    """包内相对路径：from_pkg 是引用方（xhtml），to_pkg 是图片。"""
    fdir = os.path.dirname(from_pkg)
    rel = os.path.relpath(to_pkg, fdir)
    return rel.replace(os.sep, "/")


def strip_empty_imgs(raw: bytes) -> tuple[bytes, int]:
    """删掉没有有效 src 的 <img>（<img/>、<img src=""> 等空占位符，渲染本就无内容）。

    返回 (新bytes, 删除数量)。带有效 src（含已外置的 ../Images/、外链 http）的保留。
    """
    removed = [0]

    def repl(m):
        tag = m.group(0)
        sm = re.search(rb'src\s*=\s*["\']([^"\']*)["\']', tag)
        if sm and sm.group(1).strip():
            return tag          # 有非空 src，保留
        removed[0] += 1
        return b""             # 空占位符，删掉

    out = IMG_TAG_RE.sub(repl, raw)
    return out, removed[0]


def fix_one(epub_path: str, check_only: bool, no_backup: bool, force: bool) -> str:
    z = zipfile.ZipFile(epub_path)
    names = z.namelist()
    opf_path = find_opf(z)
    if not opf_path:
        z.close()
        return "✗ 找不到 OPF（非标准 EPUB）"

    # 1) 收集所有文本 xhtml 里的 data: 图，去重处理
    text_entries = [n for n in names if n.lower().endswith(".xhtml") and "OEBPS/Text/" in n]
    # 也兼容没有 Text/ 子层的情况
    if not text_entries:
        text_entries = [n for n in names if n.lower().endswith(".xhtml")]

    uri_to_name: dict[str, str] = {}      # data uri 串 -> 图片包内路径（相对引用用）
    new_image_bytes: dict[str, bytes] = {}  # 图片包内路径 -> bytes
    new_image_ext: dict[str, str] = {}
    xhtml_changed: dict[str, bytes] = {}   # 改写的 xhtml 包内路径 -> 新 bytes

    need_work = False
    removed_total = 0
    embedded_total = 0
    names_lower = {nm.lower(): nm for nm in names}

    def store_image(raw_bytes: bytes, label: str) -> str | None:
        """把原始图片字节转成外部 jpg/png 并登记，返回包内路径；失败返回 None。"""
        try:
            im = Image.open(io.BytesIO(raw_bytes))
            im.load()
            fmt = (im.format or "png").upper()
        except Exception:
            return None
        mime = {"JPEG": "image/jpeg", "PNG": "image/png",
                "GIF": "image/gif", "WEBP": "image/webp"}.get(fmt, "image/jpeg")
        ext, out = process_image(mime, raw_bytes)
        h = hashlib.sha1(out).hexdigest()[:12]
        img_pkg = "%s/kfx_%s.%s" % (IMG_DIR, h, ext)
        if img_pkg not in new_image_bytes:
            new_image_bytes[img_pkg] = out
            new_image_ext[img_pkg] = ext
        return img_pkg

    def download(src: str) -> bytes | None:
        try:
            import urllib.request
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.read(8000000)   # 上限 8MB，防异常大图
        except Exception:
            return None

    def rewrite_src(tag: bytes, new_src: str) -> bytes:
        return re.sub(rb'src\s*=\s*["\']([^"\']*)["\']',
                      ('src="%s"' % new_src).encode("utf-8"), tag, count=1)

    def normalize_xhtml(raw: bytes, xhtml_pkg: str) -> bytes:
        nonlocal removed_total, embedded_total

        def repl(m):
            nonlocal removed_total, embedded_total
            tag = m.group(0)
            sm = re.search(rb'src\s*=\s*["\']([^"\']*)["\']', tag)
            if not sm:                      # 无 src → 删
                removed_total += 1
                return b""
            src = sm.group(1).decode("utf-8", "replace").strip()
            if not src:                     # 空 src → 删
                removed_total += 1
                return b""
            if src.startswith("data:"):     # 内联图 → 外置
                dec = decode_data_uri(src)
                if not dec:
                    removed_total += 1
                    return b""
                mime, data = dec
                ext, out = process_image(mime, data)
                h = hashlib.sha1(out).hexdigest()[:12]
                img_pkg = "%s/kfx_%s.%s" % (IMG_DIR, h, ext)
                if img_pkg not in new_image_bytes:
                    new_image_bytes[img_pkg] = out
                    new_image_ext[img_pkg] = ext
                embedded_total += 1
                return rewrite_src(tag, rel_path(xhtml_pkg, img_pkg))
            if src.startswith(("http://", "https://")):   # 外链图 → 下载内嵌，失败/不支持则删
                rawd = download(src)
                if rawd:
                    img_pkg = store_image(rawd, src)
                    if img_pkg:
                        embedded_total += 1
                        return rewrite_src(tag, rel_path(xhtml_pkg, img_pkg))
                removed_total += 1
                return b""
            # 相对引用
            base = os.path.dirname(xhtml_pkg)
            tgt = os.path.normpath(os.path.join(base, src)).replace(os.sep, "/")
            if tgt in names:
                if tgt.lower().endswith(".webp"):     # 包内 webp → 转 jpg
                    img_pkg = store_image(z.read(tgt), tgt)
                    if img_pkg:
                        embedded_total += 1
                        return rewrite_src(tag, rel_path(xhtml_pkg, img_pkg))
                return tag                            # 存在且非 webp → 保留
            key = tgt.lower()
            if key in names_lower and names_lower[key] != tgt:   # 大小写纠正
                return rewrite_src(tag, rel_path(xhtml_pkg, names_lower[key]))
            removed_total += 1                          # 真正缺失 → 删
            return b""

        return IMG_TAG_RE.sub(repl, raw)

    for n in text_entries:
        raw = z.read(n)
        new_raw = normalize_xhtml(raw, n)
        if new_raw != raw:
            need_work = True
            xhtml_changed[n] = new_raw

    z.close()

    if not need_work and not force:
        return "✓ 已友好，跳过"

    if check_only:
        return "△ 需要改写（--check-only 未执行）"

    # 3) 改写 OPF manifest，加入新图片
    with zipfile.ZipFile(epub_path) as z2:
        opf_bytes = z2.read(opf_path)
    opf_dir = os.path.dirname(opf_path)
    items_xml = b""
    for img_pkg, ext in new_image_ext.items():
        href = rel_path(opf_path, img_pkg)
        iid = "kfx_" + os.path.basename(img_pkg).rsplit(".", 1)[0]
        items_xml += ('<item id="%s" href="%s" media-type="%s"/>\n'
                      % (iid, href, media_type(ext))).encode("utf-8")
    if items_xml:
        if MANIFEST_CLOSE_RE.search(opf_bytes):
            opf_bytes = MANIFEST_CLOSE_RE.sub(items_xml + b"</manifest>", opf_bytes)
        else:
            opf_bytes = opf_bytes + items_xml

    # 4) 写新包（mimetype 必须第一个、不压缩）
    out_path = epub_path + ".fixed"
    with zipfile.ZipFile(epub_path) as z3:
        all_names = z3.namelist()
        data_cache = {}
        for nm in all_names:
            if nm in new_image_bytes:
                continue  # 旧 webp 不写，新的稍后单独写
            if nm in xhtml_changed:
                data_cache[nm] = xhtml_changed[nm]
            else:
                data_cache[nm] = z3.read(nm)
        # OPF 用改写后的
        data_cache[opf_path] = opf_bytes
        try:
            mimetype = data_cache.pop("mimetype")
        except KeyError:
            mimetype = b"application/epub+zip"
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zo:
            zo.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
            for nm in all_names:
                if nm == "mimetype":
                    continue
                if nm.lower().endswith(".webp"):
                    continue  # 删掉残留 webp
                zo.writestr(nm, data_cache[nm])
            for img_pkg, bts in new_image_bytes.items():
                zo.writestr(img_pkg, bts)

    # 5) 校验：不达标不替换原文件
    issues = check_epub_kindle(out_path)
    if issues:
        try:
            os.remove(out_path)
        except OSError:
            pass
        return "✗ 修复后仍未达标，已放弃替换：\n    " + "\n    ".join(issues[:5])

    # 6) 达标 → 备份原文件，替换
    if not no_backup:
        backup_dir = Path(epub_path).resolve().parent.parent.parent
        # 备份放到学习工厂根下的 _kindle_fix_backup
        bdir = Path("/Users/jessper/Life/学习工厂/_kindle_fix_backup")
        bdir.mkdir(parents=True, exist_ok=True)
        rel = Path(epub_path).relative_to("/Users/jessper/Life/学习工厂")
        bdst = bdir / str(rel).replace("/", "__")
        shutil.copy2(epub_path, bdst)
    shutil.move(out_path, epub_path)
    extra = ""
    if embedded_total or removed_total:
        extra = "，外置 %d / 移除修正 %d" % (embedded_total, removed_total)
    return "✓ 已重构（%s）→ Kindle 友好" % (extra or "无图片改动")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="epub 文件或目录（目录递归）")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    eps = []
    for p in args.paths:
        pp = Path(p)
        if pp.is_dir():
            eps.extend(str(x) for x in sorted(pp.rglob("*.epub")))
        else:
            eps.append(str(pp))
    if not eps:
        print("没有找到 epub")
        return 1

    ok = skip = fail = 0
    for ep in eps:
        try:
            r = fix_one(ep, args.check_only, args.no_backup, args.force)
        except Exception as exc:
            r = "✗ 异常：%s: %s" % (type(exc).__name__, exc)
        tag = r[0] if r else "?"
        if tag in ("✓",):
            ok += 1
        elif tag in ("△",):
            skip += 1
        else:
            fail += 1
        print("%s  %s" % (r, ep[-58:]))
        if tag not in ("✓", "△"):
            print("       ", r)
    print("\n汇总：重构 %d / 跳过(已友好) %d / 失败 %d" % (ok, skip, fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
