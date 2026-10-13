#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一入料入口：Notion 导出（HTML / ZIP）→ 判平台 → 判作者 → 归档 → 出书。

    ./epub.sh ingest                    扫投递箱（01、投递箱/）里的 html 与 zip
    ./epub.sh ingest <文件...>          处理指定文件
    ./epub.sh ingest --dry-run          只预演，一个文件都不落地

陈少 2026-10-13 定：后续入料有三种
    1. 微信官网直取的 HTML        —— 走原收文流程（build_epub --inbox）
    2. Notion 导出的 HTML（最多）  —— 本入口
    3. Notion 导出异常的 ZIP       —— 本入口（解开后跟 2 同构）

三种最终都产出**同一种** XHTML，落到 `02、定稿库/<平台>/<作者>/xhtml/`。
ZIP 里的中文文件名要用 cp437→utf-8 还原：Notion 打包时的编码，
shell 的 unzip 会报 "Illegal byte sequence"。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))

ROOT = ENGINE.parent
INBOX = ROOT / "01、投递箱"                     # 日常入料：丢这里
STAGING_ROOT = ROOT / "_待处理"                 # 收件箱（含历史遗留的 zip）
STAGING = STAGING_ROOT / "ingest"               # zip 解包暂存（下划线开头，不参与扫描）

import platforms as P                            # noqa: E402
import sigi_convert as S                         # noqa: E402


def safe_unzip(zpath: Path, dest: Path) -> list[Path]:
    """解开 zip，还原中文文件名。返回解出的 html 列表。

    Notion 导出的 zip 用 cp437 编码文件名，直接解会乱码甚至报
    "Illegal byte sequence"（2026-10-13 踩过），必须自己还原。
    """
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            raw = info.filename
            if info.flag_bits & 0x800:          # 已声明 UTF-8，别二次解码
                name = raw
            else:
                try:
                    name = raw.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    name = raw
            target = dest / Path(name).name
            if target.suffix.lower() not in (".html", ".htm"):
                continue
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def is_notion_export(path: Path) -> bool:
    """是不是 Notion 导出（两种格式任一）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "notion-page-content" in text or 'class="page-body"' in text or "page-body" in text


def clean_title(title: str) -> str:
    """去掉标题里的平台前缀/后缀。

    Notion 导出的标题常长这样：「知乎 - 去泰国…」「阿里白兔姐 - 小红书」。
    平台名不该进文件名（平台已经体现在目录层级里了）。
    """
    v = (title or "").strip()
    pat = r"(?:知乎|小红书|微博|Twitter|推特|Notion|微信公众号|B站|油管)"
    for _ in range(3):
        new = re.sub(r"^\s*%s\s*[-_|｜]\s*" % pat, "", v, flags=re.I)
        new = re.sub(r"\s*[-_|｜]\s*%s\s*$" % pat, "", new, flags=re.I)
        if new == v:
            break
        v = new
    return v.strip("_-— ").strip() or (title or "").strip()


def probe(path: Path) -> dict:
    """读一个 Notion 导出：来源字段、原文链接、作者、标题、日期。

    全部复用 sigi_convert 的抽取函数——规则只写一份。
    """
    from lxml import html as LH

    src = LH.fromstring(path.read_bytes(),
                        parser=LH.HTMLParser(encoding="utf-8", huge_tree=True))
    source_label = S._notion_property(src, "来源") or S._notion_property(src, "类型")
    url = S.notion_source_url(src)
    key, reason = P.resolve_platform(source_label, url)
    return {
        "path": path,
        "source_label": source_label,
        "url": url,
        "platform": key,
        "platform_reason": reason,
        "author": (S._notion_property(src, "作者") or "").strip(),
        "title": clean_title(S._base_title_from_source(src)),
        "date": S._notion_date(S._notion_property(src, "发布时间")),
        "biz": S.notion_biz(src),
    }


def archive(info: dict, dry_run: bool = False) -> Path | None:
    """归档一篇：转 XHTML → 落到 平台/作者/xhtml/。返回产出路径。"""
    from build_epub import safe_stem, find_duplicate_title, move_to_trash

    key, author = info["platform"], info["author"]
    if not key:
        print("  ✗ 认不出平台，跳过：%s（%s）" % (info["path"].name, info["platform_reason"]))
        return None
    if not author:
        print("  ✗ 认不出作者，跳过：%s" % info["path"].name)
        return None

    dest_dir = P.book_dir(key, author) / "xhtml"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 判重：标题归一化后与已有 xhtml 比对（老快照没有 sn 时唯一的办法）
    dup = find_duplicate_title(info["title"])
    if dup:
        print("  ⊘ 已在库中，跳过并清理原件：%s → %s" % (info["path"].name, dup))
        if not dry_run:
            move_to_trash(info["path"])
        return None

    stem = "_".join(x for x in (info["date"], author, info["title"]) if x)
    out = dest_dir / (safe_stem(stem) + "-Sigil.xhtml")
    if out.exists():
        print("  ⊘ 目标已存在，跳过：%s" % out.name)
        if not dry_run:
            move_to_trash(info["path"])
        return None

    if dry_run:
        print("  [预演] 会归档：%s → %s" % (info["path"].name, dest_dir.parent.name))
        return out

    S.convert(info["path"], out, "notion")
    got = out.stat().st_size if out.exists() else 0
    if got < 3000:                       # 空壳保护，跟微信线同一道闸
        out.unlink(missing_ok=True)
        print("  ✗ 转换结果疑似空壳（%d 字节），原文保留：%s" % (got, info["path"].name))
        return None
    move_to_trash(info["path"])
    print("  ✓ 归档：%s → %s/%s/" % (info["path"].name, P.PLATFORMS[key], author))
    return out


def collect_targets(args) -> list[Path]:
    """要处理的文件清单：显式给的，或投递箱 + 收件箱里扫出来的。

    两个目录都扫：投递箱是日常入口，`_待处理` 是收件箱
    （历史遗留的 Notion 导出 zip 都在那儿，不能漏）。
    """
    if args.files:
        return [Path(x).expanduser().resolve() for x in args.files]
    out: list[Path] = []
    for d in (INBOX, STAGING_ROOT):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.suffix.lower() not in (".html", ".htm", ".zip"):
                continue
            if p.parent == STAGING_ROOT and p.name == STAGING.name:
                continue
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="统一入料：Notion HTML / ZIP → 平台/作者")
    ap.add_argument("files", nargs="*", help="要处理的文件；不给则扫投递箱")
    ap.add_argument("--dry-run", action="store_true", help="只预演，不落地")
    args = ap.parse_args()

    targets = collect_targets(args)
    print("入料%s　共 %d 个文件" % ("（预演）" if args.dry_run else "", len(targets)))
    if not targets:
        print("投递箱里没有 html / zip。")
        return 0

    staged: list[Path] = []
    ok = skipped = 0
    for t in targets:
        if not t.exists():
            print("不存在，跳过：%s" % t)
            continue
        print("\n【%s】" % t.name)
        items: list[Path]
        if t.suffix.lower() == ".zip":
            if args.dry_run:
                # 预演也解到暂存区，才能报出里面是什么
                items = safe_unzip(t, STAGING / t.stem)
            else:
                items = safe_unzip(t, STAGING / t.stem)
            staged.extend(items)
            print("  解出 %d 个 html" % len(items))
        else:
            items = [t]

        for it in items:
            if not is_notion_export(it):
                print("  ⊘ 不是 Notion 导出（可能是微信直取），交给收文流程：%s" % it.name)
                skipped += 1
                continue
            info = probe(it)
            print("  来源=%-8s 链接域名=%-24s → %s（%s）"
                  % (info["source_label"] or "—", (info["url"] or "—")[:24],
                     P.PLATFORMS.get(info["platform"], "（认不出）"), info["platform_reason"]))
            print("  作者=%-12s 标题=%s" % (info["author"] or "（认不出）", info["title"][:40]))
            if archive(info, args.dry_run):
                ok += 1

    print("\n合计：归档 %d，非 Notion 跳过 %d" % (ok, skipped))
    if args.dry_run:
        print("（预演，未落地。确认后去掉 --dry-run 执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
