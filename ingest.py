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

# 覆盖更新模式（陈少 2026-10-14）：重复项重解析后覆盖既有 xhtml，而非删除原件。
OVERWRITE_MODE = False


def _pg(name: str, state: str, detail: str = "", target: str = "",
        section: str = "article") -> None:
    if _PRG is not None:
        _PRG.set_state(name, state, detail, target)
        # 同步进板块视图；默认「文章合集」，出版图书走「出版图书」板块
        _PRG.section_item(section, name, state, detail, target)


def _month_dir(dest_dir: Path, date_text: str) -> Path:
    """按「年+月」给 dest_dir 挂一层月份子目录（陈少 2026-10-16 定，与 build_epub.month_subdir 同源）。

    date_text 形如「2022年10月26日」，取不到/非标准就返回 dest_dir 原样（仍平铺）。
    """
    try:
        from build_epub import month_subdir
        msub = month_subdir(date_text)
    except Exception:
        msub = ""
    return dest_dir / msub if msub else dest_dir


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


def _is_webarchive(path: Path) -> bool:
    """是不是网页存档（WordPress 保存网页，如猫笔刀 maobidao.cn）。

    特征：<base href> + entry-content 正文容器。复用 pipeline 的 webarchive adapter 判定。
    """
    if path.suffix.lower() not in (".html", ".htm"):
        return False
    try:
        import core.adapters        # noqa: F401  触发注册
        from core.pipeline import pick
        ad = pick(path)
        return ad is not None and ad.name == "webarchive"
    except Exception:
        return False


KOODO_CONTENT_MARKER = "koodo reader - highlights"  # 与 reading_notes.py 一致（不区分大小写）


def _looks_koodo_notes(path: Path) -> bool:
    """是不是 Koodo 读书笔记 html（内容含「Koodo Reader - Highlights」完整标识）。

    ingest 防抢跑兜底（陈少 2026-10-16 定）：读书笔记段① 本应在 ingest 之前把它搬走，
    但万一 ingest 先扫到（时序/路径异常），认出来就跳过、不丢 _待判，交给读书笔记段处理。
    """
    if path.suffix.lower() != ".html":
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return KOODO_CONTENT_MARKER in text.lower()


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
    # 出版图书线（陈少 2026-10-14 定）：html 带「书名」或「Rank」属性 → 走出版图书流程。
    book_title = (S._notion_property(src, "书名") or "").strip()
    rank = (S._notion_property(src, "Rank") or "").strip()
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
        "book_title": book_title,
        "rank": rank,
        "is_book": bool(book_title or rank),  # 有书名或 Rank 任一 → 出版图书
    }


# 本次 run 归档过的号（陈少 2026-10-14：处理投递箱后要精准重建这些号的 epub）
TOUCHED_ACCOUNTS: set[str] = set()


def _locate_existing(where: str) -> Path | None:
    """把 link_index/find_duplicate_title 的 where 描述定位到既有 xhtml 完整路径。

    where 两种格式：`平台/作者/文件名`（link_index）或 `书目录名/文件名`（标题判重）。
    统一处理：取最后一段当文件名，遍历所有 book_dir 找匹配。
    源可能按「年+月」分子目录，所以用递归 rglob 而不是只查平铺层。
    """
    fname = where.split("/")[-1]
    import build_epub as B
    for book in B.collect_book_dirs():
        for cand in B.xhtml_all(B.source_dir(book)):
            if cand.name == fname:
                return cand
    return None


def _overwrite_existing(info: dict, where: str, dry_run: bool) -> Path | None:
    """覆盖更新：用投递箱的新 html 重新解析，覆盖既有那篇 xhtml（陈少 2026-10-14）。

    - 只覆盖 where 指向的那一篇；文件名随新日期/标题变（旧名删）。
    - 保留投递箱原件（不删）。
    """
    from build_epub import safe_stem
    old = _locate_existing(where)
    if old is None:
        print("  ⚠ 覆盖更新：定位不到既有 xhtml（%s），跳过覆盖" % where)
        return None
    if dry_run:
        print("  [预演] 会覆盖更新：%s → 重解析覆盖 %s" % (info["path"].name, old.name[:40]))
        return old

    dest_dir = old.parent
    key, author = info["platform"], info["author"]
    stem = "_".join(x for x in (info["date"], author, info["title"]) if x)
    new_out = dest_dir / (safe_stem(stem) + "-Sigil.xhtml")

    # 文件名变了 → 删旧文件
    if new_out != old and old.exists():
        old.unlink()
        print("  · 旧文件删除（名变了）：%s" % old.name[:40])

    S.convert(info["path"], new_out, "notion")
    # 不判「空壳」：哪怕只有 1 个字也照常覆盖（陈少 2026-10-16 定）；
    # 仅极端情况（正文完全 0 字且无图）才放弃，防空白覆盖好内容
    from build_epub import is_empty_shell
    shell, chars = is_empty_shell(new_out)
    if shell and chars == 0:
        print("  ✗ 覆盖更新：新 html 正文完全为空，放弃覆盖")
        return None

    # 更新留底 html
    # 号目录要稳定取（不能用 dest_dir.parent：dest_dir 可能是 xhtml/2022年10月/ 这种月份子目录，
    # parent 会拿到 xhtml/，html 留底就落错到 xhtml/html/）
    book_dir = P.book_dir(key, author)
    html_dir = book_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    kept = html_dir / (safe_stem(stem) + ".html")
    shutil.copy2(info["path"], kept)      # 覆盖留底

    _pg(info["path"].name, "ok", "", "%s" % book_dir.name)
    print("  ✓ 覆盖更新：%s → %s" % (info["path"].name, new_out.name[:40]))
    TOUCHED_ACCOUNTS.add((key, author))
    return new_out


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
        if OVERWRITE_MODE:
            return _overwrite_existing(info, hit, dry_run)
        _pg(info["path"].name, "dup", "链接已收录于 %s" % hit)
        print("  ⊘ 链接已收录，跳过并清理原件：%s → %s" % (info["path"].name, hit))
        if not dry_run:
            move_to_trash(info["path"])
        return None

    dup = find_duplicate_title(info["title"])
    if dup:
        if OVERWRITE_MODE:
            return _overwrite_existing(info, dup, dry_run)
        _pg(info["path"].name, "dup", "标题重复于 %s" % dup)
        print("  ⊘ 已在库中，跳过并清理原件：%s → %s" % (info["path"].name, dup))
        if not dry_run:
            move_to_trash(info["path"])
        return None

    dest_dir = P.book_dir(key, author) / "xhtml"
    stem = "_".join(x for x in (info["date"], author, info["title"]) if x)
    # 按「年+月」分子目录（陈少 2026-10-16 定，与微信/网页存档入料一致）
    dest_dir = _month_dir(dest_dir, info["date"])
    dest_dir.mkdir(parents=True, exist_ok=True)
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
    # 不判「空壳」：哪怕只有 1 个字也照常归档（陈少 2026-10-16 定）；
    # 仅极端情况（正文完全 0 字且无图）才放弃，防空白进定稿库
    shell, chars = is_empty_shell(out)
    if shell and chars == 0:
        out.unlink(missing_ok=True)
        _pg(info["path"].name, "fail", "正文完全为空")
        print("  ✗ 转换结果正文完全为空，原文保留：%s" % info["path"].name)
        return None
    # 登记链接 ID：原件马上要删，之后再想补索引就没来源了
    link_index.register(info.get("url", ""),
                        "%s/%s/%s" % (P.PLATFORMS[key], author, out.name))

    TOUCHED_ACCOUNTS.add((key, author))

    # ── html 原始留底（陈少 2026-10-13 定）：入料时 copy 一份，原件仍进废纸篓 ──
    # 说明：PDF 线**暂停**（陈少 2026-10-13：PDF 暂不成熟，后续再想）。
    # html 留底保留，将来 PDF 按需从 html/ 重出（`./epub.sh pdf`）。
    book_dir = P.book_dir(key, author)
    html_dir = book_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    kept = html_dir / (safe_stem(stem) + ".html")
    if not kept.exists():
        shutil.copy2(info["path"], kept)
        print("  ✓ 留底：%s/html/%s" % (P.PLATFORMS[key], kept.name[:40]))

    move_to_trash(info["path"])
    _pg(info["path"].name, "ok", "", "%s/%s" % (P.PLATFORMS[key], author))
    print("  ✓ 归档：%s → %s/%s/" % (info["path"].name, P.PLATFORMS[key], author))
    return out


# 出版图书硬编码兜底（陈少 2026-10-14：第一批《交易之王》还没加书名/作者字段，先硬编码）
BOOK_FALLBACK_TITLE = "交易之王"
BOOK_FALLBACK_AUTHOR = "Xtony"


def archive_book(info: dict, dry_run: bool = False) -> Path | None:
    """归档一篇「出版图书」章节：转 XHTML → 落到 08、出版图书/<书名>/xhtml/。

    规则（陈少 2026-10-14 定，长期）：
      - 书名 = 属性「书名」（空则硬编码兜底「交易之王」）
      - 作者 = 属性「作者」（空则兜底「Xtony」）
      - 排序 = Rank（文件名用 rank 前缀保证顺序）
      - 章节标题 = 原文标题（h2 平级，rank 不进 epub）
    """
    from build_epub import safe_stem

    book_title = info.get("book_title") or BOOK_FALLBACK_TITLE
    author = info.get("author") or BOOK_FALLBACK_AUTHOR
    rank = info.get("rank") or "99"

    # 目录 = 08、出版图书/<书名>/
    dest_dir = P.platform_dir("book") / safe_stem(book_title) / "xhtml"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 文件名：rank 前缀 + 标题（rank 只用于排序，不进 epub 章节名）
    title = info["title"]
    stem = "%02d_%s" % (int(rank) if rank.isdigit() else 99, safe_stem(title))
    out = dest_dir / (stem + "-Sigil.xhtml")
    if out.exists():
        print("  ⊘ 目标已存在，跳过：%s" % out.name)
        if not dry_run:
            from build_epub import move_to_trash
            move_to_trash(info["path"])
        return None

    if dry_run:
        print("  [预演] 会归档（出版图书）：%s → %s" % (info["path"].name, out.name))
        return out

    S.convert(info["path"], out, "notion")

    # 书章节标题 h1 → h2（出版图书全 h2 平级）
    _book_add_h1_title(out)

    # html 留底
    book_dir = P.platform_dir("book") / safe_stem(book_title)
    html_dir = book_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    kept = html_dir / (stem + ".html")
    if not kept.exists():
        shutil.copy2(info["path"], kept)

    from build_epub import move_to_trash
    move_to_trash(info["path"])
    _pg(info["path"].name, "ok", "", "08、出版图书/%s" % book_title, section="book")
    print("  ✓ 归档（出版图书）：%s → 08、出版图书/%s/" % (info["path"].name, book_title))

    # 登记进重建清单（book 平台，书名作 key）
    TOUCHED_ACCOUNTS.add(("book", book_title))
    return out


def _book_add_h1_title(xhtml_path: pathlib.Path) -> None:
    """出版图书章节：把页面 <title> 提升为正文 h1 章节标题（打包时单月压平自然成 h2 层级）。

    S.convert 产出的章节标题是 h3.section-title（Notion sub_header），
    且页面 title 只写在 <title> 标签里。这里把 <title> 作为 h2 插到 body 开头，
    并去掉正文里与章节名重复的第一个 h3.section-title。
    """
    from lxml import etree
    tree = etree.parse(str(xhtml_path), etree.XMLParser(resolve_entities=False, no_network=True))
    root = tree.getroot()
    ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
    XN = ("{%s}" % ns) if ns else ""
    def tag(el):
        return el.tag.split("}")[-1]
    # 章节名 = <title>
    title_el = root.find(".//%stitle" % XN)
    chap = (title_el.text or "").strip() if title_el is not None else ""
    if not chap:
        return
    body = root.find(".//%sbody" % XN)
    if body is None:
        return
    # 在 body 开头插 h2
    h1 = etree.SubElement(body, XN + "h1")
    h1.text = chap
    body.insert(0, h1)
    # 去掉正文里第一个与章节名重复的 h3.section-title
    for el in list(body):
        if tag(el) == "h3" and (el.text or "").strip() == chap:
            body.remove(el)
            break
    xhtml_path.write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True))


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
        # 记录到重建集合（陈少 2026-10-14：微信链路入料后也要自动重建 epub）。
        # 微信文章走 4 段式文件名（日期_号_作者_标题），号名即 account 第 1 段；
        # 但 TOUCHED_ACCOUNTS 存的是 (平台key, 作者)，作者要从文件名取。
        TOUCHED_ACCOUNTS.add(("wechat", account))
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
    log = ROOT / "_管理" / "_投递箱处理日志.md"
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
    ap.add_argument("--overwrite", action="store_true",
                    help="覆盖更新：重复项（标题或链接命中）重解析后覆盖既有 xhtml（陈少 2026-10-14）")
    args = ap.parse_args()

    # 覆盖更新模式：全局开关（archive 里判重复时用）。
    # 同时打开 build_epub.FORCE_PROCESS —— 微信链路（_add_one）靠它实现覆盖重转，
    # 两条链路共用同一套「重复即覆盖」语义（陈少 2026-10-14）。
    global OVERWRITE_MODE
    OVERWRITE_MODE = args.overwrite
    if args.overwrite:
        import build_epub as _B
        _B.FORCE_PROCESS = True

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
            # 形态分流：微信直取 / 网页存档 / Notion 导出 / 都不像
            if not is_notion_export(it):
                if is_wechat_html(it) or _is_webarchive(it):
                    kind = "微信直取" if is_wechat_html(it) else "网页存档"
                    print("  形态=%s → 走文章链路" % kind)
                    _pg(it.name, "running", "文章链路")
                    if handle_wechat(it, args.dry_run):
                        ok += 1
                    continue
                # 既不是 Notion 也不是微信 → 先查是不是 Koodo 读书笔记（防抢跑兜底，陈少 2026-10-16 定）
                if _looks_koodo_notes(it):
                    print("  形态=Koodo 读书笔记（内容含 Koodo Reader - Highlights），跳过——交由读书笔记段处理")
                    _pg(it.name, "skip", "Koodo 读书笔记，交由读书笔记段")
                    continue
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
            if info.get("is_book"):
                # 出版图书线：有「书名」或「Rank」属性 → 走出版图书归档
                print("  类型=出版图书（书名=%s rank=%s）" % (info.get("book_title") or "（空）",
                      info.get("rank") or "（空）"))
                if archive_book(info, args.dry_run):
                    ok += 1
            elif archive(info, args.dry_run):
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

    # ── 入料后精准重建 epub（陈少 2026-10-14：处理投递箱要顺带把 epub 更新了）──
    # 只重建本次归档过的号，不碰没变的号（方案 B）。
    if not args.dry_run and TOUCHED_ACCOUNTS:
        import build_epub as B
        print("\n────────── 重建 epub ──────────")
        _PRG.set_state("（入料完成）", "ok", "开始更新 epub")
        built = 0
        for key, author in sorted(TOUCHED_ACCOUNTS):
            book_dir = P.book_dir(key, author)
            try:
                outs = B.build_all(author, book_dir)
                if outs:
                    built += len(outs)
                    for o in outs:
                        print("  ✓ %s" % o.name)
                        _PRG.set_book(o.name)      # 进度页能看到每本更新了的 epub
            except Exception as exc:      # 单号重建失败不能拖垮整批
                print("  ✗ 重建失败：%s | %s: %s" % (author, type(exc).__name__, str(exc)[:80]))
                _PRG.set_book(author, "fail", str(exc)[:60])
        print("重建 %d 本。" % built)
        _PRG.set_state("（入料完成）", "ok", "已更新 %d 本 epub" % built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
