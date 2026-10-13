#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一入料入口：**三种入料一个口**（陈少 2026-10-13 定）。

    ./epub.sh ingest                    扫投递箱（01、投递箱/）里的 html 与 zip
    ./epub.sh ingest <文件...>          处理指定文件
    ./epub.sh ingest --dry-run          只预演，一个文件都不落地

三种入料
    1. 微信官网直取的 HTML        —— 走微信链路（build_epub 那套，含 biz 归号）
    2. Notion 导出的 HTML（最多）  —— 平台判定 → 作者 → 归档
    3. Notion 导出异常的 ZIP       —— 解开后与第 2 种同构

三种最终都产出**同一种** XHTML，落到 `02、定稿库/<平台>/<作者>/xhtml/`。
微信并不单独一条路——本入口自己认形态，用户永远只跑一条命令。

判不出的处理（陈少 2026-10-13 定）：
    没「来源」字段就用原文链接判；两个都没有 → 落到
    `02、定稿库/_待判/`，在报告里列出来提醒人工处理（**不猜**）。

ZIP 里的中文文件名要用 cp437→utf-8 还原：Notion 打包时的编码，
shell 的 unzip 会报 "Illegal byte sequence"。
"""

from __future__ import annotations

import argparse
import datetime as dt
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
# 判不出平台/作者的落这儿，等人工看一眼。下划线开头 → 不参与成书扫描。
UNKNOWN_DIR = ROOT / "02、定稿库" / "_待判"

import platforms as P                            # noqa: E402
import link_index                                 # noqa: E402
import progress                                   # noqa: E402
import sigi_convert as S                         # noqa: E402

# 本轮的进度对象。默认为空实现（避免各处都要判 None）。
_PRG: "progress.Progress | None" = None


def _pg(name: str, state: str, detail: str = "", target: str = "") -> None:
    if _PRG is not None:
        _PRG.set_state(name, state, detail, target)


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


def is_wechat_html(path: Path) -> bool:
    """是不是微信官网直取的文章。判定规则复用 build_epub，不重写一份。"""
    try:
        import build_epub as B
        return B.looks_like_wechat(path)
    except Exception:
        return False


def to_unknown(path: Path, reason: str, dry_run: bool) -> None:
    """判不出的文件挪到 `_待判/`，等人工处理（陈少 2026-10-13 定：不猜）。"""
    _pg(path.name, "unknown", reason)
    print("  ⚠ 判不出平台/作者 → 待判目录（%s）" % reason)
    if dry_run:
        return
    UNKNOWN_DIR.mkdir(parents=True, exist_ok=True)
    dest = UNKNOWN_DIR / path.name
    n = 1
    while dest.exists():                        # 同名不覆盖，加序号
        dest = UNKNOWN_DIR / ("%s_%d%s" % (path.stem, n, path.suffix))
        n += 1
    shutil.move(str(path), str(dest))
    print("     → %s" % dest.name)


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
    """归档一篇 Notion 导出：转 XHTML → 落到 平台/作者/xhtml/。返回产出路径。

    判不出平台或作者 → 交给 to_unknown 落待判目录（陈少 2026-10-13 定：不猜）。
    """
    from build_epub import (safe_stem, find_duplicate_title, move_to_trash,
                            is_empty_shell)

    key, author = info["platform"], info["author"]
    if not key or not author:
        reason = info["platform_reason"] if not key else "属性表没有「作者」字段"
        to_unknown(info["path"], reason, dry_run)
        return None

    # 判重两道，先链接后标题：
    #   链接级（`_链接索引.json`）—— 同一篇不论标题怎么写都能认出来，
    #                                是 2026-10-13 加的那道，专治"迁移版 vs Notion 原件"这类漏判
    #   标题级（`find_duplicate_title`）—— 没有链接的老快照靠它
    hit = link_index.find(info.get("url", ""))
    if hit:
        _pg(info["path"].name, "dup", "链接已收录于 %s" % hit)
        print("  ⊘ 链接已收录，跳过并清理原件：%s → %s" % (info["path"].name, hit))
        if not dry_run:
            move_to_trash(info["path"])
        return None

    dup = find_duplicate_title(info["title"])
    if dup:
        _pg(info["path"].name, "dup", "标题重复于 %s" % dup)
        print("  ⊘ 已在库中，跳过并清理原件：%s → %s" % (info["path"].name, dup))
        if not dry_run:
            move_to_trash(info["path"])
        return None

    dest_dir = P.book_dir(key, author) / "xhtml"
    dest_dir.mkdir(parents=True, exist_ok=True)

    stem = "_".join(x for x in (info["date"], author, info["title"]) if x)
    out = dest_dir / (safe_stem(stem) + "-Sigil.xhtml")
    if out.exists():
        _pg(info["path"].name, "dup", "目标已存在")
        print("  ⊘ 目标已存在，跳过：%s" % out.name)
        if not dry_run:
            move_to_trash(info["path"])
        return None

    if dry_run:
        print("  [预演] 会归档：%s → %s" % (info["path"].name, dest_dir.parent.name))
        return out

    S.convert(info["path"], out, "notion")
    # 空壳判定看**正文字数**（不用字节数——Twitter/微博短帖正文两三百字是正常的）
    shell, chars = is_empty_shell(out)
    if shell:
        out.unlink(missing_ok=True)
        _pg(info["path"].name, "fail", "疑似空壳（正文仅 %d 字）" % chars)
        print("  ✗ 转换结果疑似空壳（正文仅 %d 字），原文保留：%s" % (chars, info["path"].name))
        return None
    # 登记链接 ID：原件马上要删，之后再想补索引就没来源了
    link_index.register(info.get("url", ""),
                        "%s/%s/%s" % (P.PLATFORMS[key], author, out.name))
    move_to_trash(info["path"])
    _pg(info["path"].name, "ok", "", "%s/%s" % (P.PLATFORMS[key], author))
    print("  ✓ 归档：%s → %s/%s/" % (info["path"].name, P.PLATFORMS[key], author))
    return out


def handle_wechat(path: Path, dry_run: bool) -> Path | None:
    """微信直取的 HTML：交给 build_epub 那条成熟链路（含 biz 归号、推广图清理）。

    在这里**不重写一套**——微信的号名识别（js_name + biz 映射）、推广图黑名单、
    空壳保护、判重废纸篓都在那边，重复实现必然分叉。本函数只做"转发"。
    """
    import build_epub as B

    before = len(B.STATS["added"])
    touched: dict = {}
    try:
        B._add_one(path, archive_original=True, dry_run=dry_run, touched=touched)
    except Exception as exc:              # 单篇炸了不能让整批停
        _pg(path.name, "fail", "%s: %s" % (type(exc).__name__, str(exc)[:60]))
        print("  ✗ 微信转换异常：%s: %s" % (type(exc).__name__, str(exc)[:100]))
        return None
    if len(B.STATS["added"]) > before:
        account, fname = B.STATS["added"][-1].split(" / ", 1)
        _pg(path.name, "ok", "", account)
        print("  ✓ 归档：%s → %s/xhtml/" % (path.name, account))
        return Path(fname)
    _pg(path.name, "dup", "已存在或在库中")
    print("  ⊘ 未新增（已存在或在库中）：%s" % path.name)
    return None


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


def _write_run_log(started: dt.datetime, ok: int, unknown: int, skipped: int,
                   note: str = "") -> None:
    """往 `_投递箱处理日志.md` 追一行。

    为什么单独一个日志：定时任务是无人值守跑的（launchd），它不写 `_处理报告.md`
    （那是手动跑才写的）。没有这个文件，陈少双击后根本分不清
    "定时跑的"和"我自己跑的"、也看不到上次是什么时候（2026-10-13 陈少问）。
    """
    log = ROOT / "_投递箱处理日志.md"
    line = "| %s | %d | %d | %d | %s |\n" % (
        started.strftime("%Y-%m-%d %H:%M"), ok, unknown, skipped, note or "—")
    try:
        if not log.exists():
            log.write_text(
                "# 投递箱处理日志\n\n"
                "每次 ingest（含定时任务）追加一行。看最近几次就知道有没有在跑。\n\n"
                "| 时间 | 归档 | 待判 | 跳过 | 备注 |\n"
                "|---|---|---|---|---|\n", encoding="utf-8")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="统一入料：微信 / Notion HTML / ZIP → 平台/作者")
    ap.add_argument("files", nargs="*", help="要处理的文件；不给则扫投递箱")
    ap.add_argument("--dry-run", action="store_true", help="只预演，不落地")
    args = ap.parse_args()

    targets = collect_targets(args)
    started = dt.datetime.now()
    print("入料%s　共 %d 个文件" % ("（预演）" if args.dry_run else "", len(targets)))

    # 进度落盘，供浏览器页面轮询（陈少 2026-10-13：要实时看到哪个处理过了）
    global _PRG
    _PRG = progress.Progress("投递箱处理", dry_run=args.dry_run)

    if not targets:
        print("投递箱里没有 html / zip。")
        _PRG.finish("投递箱是空的")
        _write_run_log(started, 0, 0, 0, note="无待处理文件")
        return 0

    # 先列出全部待处理（zip 里的要解开才知道，所以先列顶层文件）
    _PRG.plan([t.name for t in targets])

    staged: list[Path] = []
    ok = skipped = unknown = 0
    for t in targets:
        if not t.exists():
            print("不存在，跳过：%s" % t)
            continue
        _pg(t.name, "running")
        print("\n【%s】" % t.name)
        items: list[Path]
        if t.suffix.lower() == ".zip":
            # 预演也解到暂存区，才能报出里面是什么
            items = safe_unzip(t, STAGING / t.stem)
            staged.extend(items)
            print("  解出 %d 个 html" % len(items))
            for sub in items:               # zip 内部文件也列进进度页
                _pg(sub.name, "waiting", "来自 %s" % t.name)
        else:
            items = [t]

        for it in items:
            # 形态分流：微信直取 / Notion 导出 / 都不像
            if not is_notion_export(it):
                if is_wechat_html(it):
                    print("  形态=微信直取 → 走微信链路")
                    _pg(it.name, "running", "微信链路")
                    if handle_wechat(it, args.dry_run):
                        ok += 1
                    continue
                # 既不是 Notion 也不是微信 → 交给人工
                print("  形态=认不出（既不是 Notion 导出，也不像微信文章）")
                to_unknown(it, "不是可识别的微信/Notion 文章", args.dry_run)
                unknown += 1
                continue
            _pg(it.name, "running")
            info = probe(it)
            print("  来源=%-8s 链接域名=%-24s → %s（%s）"
                  % (info["source_label"] or "—", (info["url"] or "—")[:24],
                     P.PLATFORMS.get(info["platform"], "（认不出）"), info["platform_reason"]))
            print("  作者=%-12s 标题=%s" % (info["author"] or "（认不出）", info["title"][:40]))
            if archive(info, args.dry_run):
                ok += 1
            elif not info["platform"] or not info["author"]:
                unknown += 1

    print("\n合计：归档 %d，待判 %d，跳过 %d" % (ok, unknown, skipped))
    if unknown:
        print("  ⚠ 有 %d 个落进待判目录，需要人工看一眼：%s" % (unknown, UNKNOWN_DIR))
    if args.dry_run:
        print("（预演，未落地。确认后去掉 --dry-run 执行）")
    else:
        _write_run_log(started, ok, unknown, skipped,
                       note="手动" if args.files else "投递箱")
    _PRG.finish("归档 %d，待判 %d，跳过 %d" % (ok, unknown, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
