#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读书笔记合并：Koodo Reader 划线 html + Kindle My Clippings.txt → 每本书一个 XHTML。

统一入口，单脚本处理两类来源（陈少 2026-10-15 定，2026-10-15 由两套脚本合并而来）：
- Koodo：投递箱里文件名含 KoodoReader 或 内容含 Koodo Reader 的 html
- Kindle：投递箱里文件名含 clipping 的 txt（Kindle 导出的 My Clippings）

规则（与陈少对齐，长期）：
- 一本书一个文件夹（文件夹名 = 书名），Koodo 落在 `Koodo Reader 读书笔记/<书名>/`，
  Kindle 落在 `Kindle 读书笔记/<书名>/`；Kindle 原始 txt 统一收进 `Kindle 读书笔记/_原始/`。
- 一本书多个来源文件 → 合并成一个 `<书名>.xhtml`；Koodo 原始 html 保留在书文件夹，Kindle 原始 txt 在 _原始/。
- 源文件必须被「消费」：投递箱里的 `My Clippings.txt` / Koodo html 处理后归档到 `_原始/` / 书文件夹；同名冲突按内容判定——完全相同丢弃投递箱副本、不同则递增重命名（如 `My Clippings (2).txt`）归档，**绝不留在投递箱**（否则下次点 command 看似「没反应」，且 Kindle 重新导出的新版笔记会被漏掉）。
- 去重：按「规范化原文」跨文件/跨章节全局去重，**一条只留一条**，保留第一次时间戳。
- 部分重复标注（陈少 2026-10-16 定）：精确重复只留一份后，仍对每本书笔记两两比对——一条是另一条的子串（互相包含）或相似度 ≥0.85 即标为「疑似部分重复」；xhtml 里加橙色标记 + 「⚠ 疑似部分重复，待核对」小字，并出 `09、读书笔记/_部分重复_待核对.md` 汇总供人工核对取舍（Koodo / Kindle 共用此逻辑）。
- 不保留 Color；每条笔记的日期保留（Koodo 的 Date / Kindle 的添加于）。
- 结构：Koodo 书名 h1 → 章节 h2 → 笔记 blockquote + meta；Kindle 无章节，h1 后笔记平铺。
- 不判断「空壳」：html/txt 哪怕内容极少（只有 1 个字的笔记、或只有书名没有笔记）也照常处理；书名解析不到时用文件名兜底；是否空壳由陈少人工判断，脚本不擅自跳过（空文本笔记仍按去重层跳过）。
- 暂不进 EPUB。

用法：
    python reading_notes.py --inbox [投递箱]     扫投递箱，自动分流 Koodo/Kindle 并处理（不填走默认 01、投递箱）
    python reading_notes.py --file <path>        处理单个文件（按扩展名/内容识别 Koodo/Kindle）
    python reading_notes.py --rebuild            重合并 Koodo + Kindle 全部笔记
    python reading_notes.py --koodo  (配合 --inbox/--rebuild)   只处理 Koodo 部分
    python reading_notes.py --kindle (配合 --inbox/--rebuild)   只处理 Kindle 部分
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import html as _html
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from lxml import etree

# 进度上报（跨 skill 共用 progress.py；缺失时静默降级）
try:
    from progress import report as _report
except Exception:
    def _report(*a, **k):
        pass

# ─── 路径常量 ───
LINE_ROOT = Path("/Users/jessper/Life/学习工厂/02、文稿成品线")
NOTES_ROOT = LINE_ROOT / "02、定稿库" / "09、读书笔记"
KOODO_ROOT = NOTES_ROOT / "Koodo Reader 读书笔记"
KINDLE_ROOT = NOTES_ROOT / "Kindle 读书笔记"
INBOX = LINE_ROOT / "01、投递箱"

# ─── 识别标记 ───
KOODO_PREFIX = "KoodoReader-Highlight"
KOODO_NAME_MARKER = "koodoreader"      # 文件名含此串 → Koodo（无空格，如 KoodoReader-Highlight-<ts>.html）
KOODO_CONTENT_MARKER = "koodo reader - highlights"  # 内容含此完整标识 → Koodo（有空格有连字符，如 <title>Koodo Reader - Highlights</title>；收紧避免误伤正文偶含 "koodo reader" 的普通文章）
CLIPPING_MARKER = "clipping"           # 文件名含此串 → Kindle（如 My Clippings.txt）
ORIGIN_DIRNAME = "_原始"

XHTML_NS = "http://www.w3.org/1999/xhtml"

# 部分重复判定（陈少 2026-10-16 定）：一条笔记是另一条的子串（互相包含），或两条相似度≥此阈值，
# 即标为「疑似部分重复」交给陈少人工核对（精确重复已在 merge_books 里只留一份）。
PARTIAL_DUP_RATIO = 0.85
DUP_REPORT_PATH = NOTES_ROOT / "_部分重复_待核对.md"
DUP_STATE_PATH = NOTES_ROOT / "_部分重复_核对状态.json"
# 部分重复命中收集，key=(书名, 规范化A, 规范化B) 天然去重；跑完由 write_dup_report 落盘。
DUP_FINDINGS: dict = {}


# ════════════ 共享工具 ════════════

def _norm_text(text: str) -> str:
    """规范化笔记原文用于去重：压缩所有空白（含换行/连续空格）为单空格并去首尾空白。"""
    return re.sub(r"\s+", " ", (text or "").strip())


def _safe_name(book: str) -> str:
    """把书名清洗成安全文件名/文件夹名：去掉 BOM、控制字符、首尾空白/点，
    把会破坏路径的 / \\ 换成 ·（书名原样仅用于展示 h1/title，不进路径）。"""
    s = (book or "").replace("\ufeff", "").replace("\r", "")
    for ch in ("/", "\\"):
        s = s.replace(ch, "·")
    # 去掉 C 类控制字符（Cc/Cf/Cs/Co/Cn），保留正常空白
    s = "".join(c for c in s if c == " " or not unicodedata.category(c).startswith("C"))
    s = s.strip().strip(".")
    return s or "未命名"


def _similarity(a: str, b: str) -> float:
    """两条笔记规范化原文的相似度（0~1），用 difflib 比率。"""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_partial_dup_pairs(notes: list[dict]) -> list:
    """在（已精确去重的）笔记列表里找「部分重复」对，返回 [(i, j), ...]（i<j）。

    判定：A 是 B 的子串（互相包含）一律算；否则相似度 ≥ PARTIAL_DUP_RATIO 也算。
    完全相等不会进来（精确去重已先去掉）。
    """
    pairs = []
    norms = [_norm_text(x.get("text", "")) for x in notes]
    n = len(notes)
    for i in range(n):
        ai = norms[i]
        if not ai:
            continue
        for j in range(i + 1, n):
            bj = norms[j]
            if not bj:
                continue
            if ai in bj or bj in ai or _similarity(ai, bj) >= PARTIAL_DUP_RATIO:
                pairs.append((i, j))
    return pairs


def merge_books(parsed_list: list[dict]) -> dict:
    """按书名合并多份解析结果，跨文件/跨章节按「规范化原文」去重，只留第一次出现（保留其章节与日期）。"""
    merged: dict[str, dict] = {}
    seen_by_book: dict[str, set] = {}
    for parsed in parsed_list:
        for book, data in parsed.items():
            if not book:
                continue
            if book not in merged:
                merged[book] = {"author": data.get("author", ""), "notes": []}
                seen_by_book[book] = set()
            elif not merged[book].get("author") and data.get("author"):
                merged[book]["author"] = data["author"]
            seen = seen_by_book[book]
            for n in data.get("notes", []):
                key = _norm_text(n["text"])
                if not key:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                merged[book]["notes"].append(n)
    # 部分重复标注：在「精确去重」之后，对每本书笔记两两比对，命中打 dup 并收集进报告
    for book, data in merged.items():
        notes = data.get("notes", [])
        if len(notes) < 2:
            continue
        for i, j in find_partial_dup_pairs(notes):
            notes[i]["dup"] = True
            notes[j]["dup"] = True
            na = _norm_text(notes[i].get("text", ""))
            nb = _norm_text(notes[j].get("text", ""))
            DUP_FINDINGS[(book, na, nb)] = {
                "book": book,
                "author": data.get("author", ""),
                "a": notes[i],
                "b": notes[j],
            }
    return merged


def _archive_source(src: Path, dest_dir: Path, name: str) -> Path | None:
    """把投递箱里的源文件归档到 dest_dir/name，保证源一定被「消费」掉（绝不留在投递箱）：
    - 与已存在文件字节完全相同 → 直接删掉源副本（已归档过，不重复），返回已存在目标；
    - 同名但内容不同（如 Kindle 重新导出的新版）→ 递增重命名（name (k)）归档，确保新内容被合并；
    - 不存在 → 正常移动。
    """
    if not src.exists():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if src.resolve() == dest.resolve():
        return dest  # 源已在目标位置（如 rebuild 传文件夹内文件）
    if dest.exists():
        try:
            same = dest.read_bytes() == src.read_bytes()
        except OSError:
            same = False
        if same:
            src.unlink()
            print(f"  与 {dest_dir.name}/{name} 完全相同，丢弃投递箱重复副本")
            return dest
        base, suffix = dest.stem, dest.suffix
        k = 1
        uniq = dest_dir / f"{base} ({k}){suffix}"
        while uniq.exists():
            k += 1
            uniq = dest_dir / f"{base} ({k}){suffix}"
        shutil.move(str(src), str(uniq))
        print(f"  原始留底 → {dest_dir.name}/{uniq.name}（与同名旧版不同，已另存）")
        return uniq
    shutil.move(str(src), str(dest))
    print(f"  原始留底 → {dest_dir.name}/{dest.name}")
    return dest


def _trash(path: Path) -> None:
    """把源文件丢系统废纸篓（可恢复），优先 /usr/bin/trash，回退 ~/.Trash。"""
    trash_bin = shutil.which("trash")
    if trash_bin:
        subprocess_run_trash(trash_bin, path)
        return
    dest = Path.home() / ".Trash" / path.name
    n = 1
    while dest.exists():
        dest = Path.home() / ".Trash" / f"{path.stem} ({n}){path.suffix}"
        n += 1
    shutil.move(str(path), str(dest))


def subprocess_run_trash(trash_bin: str, path: Path) -> None:
    import subprocess as _sp
    try:
        _sp.run([trash_bin, str(path)], check=True)
    except Exception:
        dest = Path.home() / ".Trash" / path.name
        shutil.move(str(path), str(dest))


def _lname(el) -> str:
    """取元素 local name（不依赖命名空间，HTMLParser 解析出无命名空间的 tag）。"""
    tag = el.tag
    return tag.split("}")[-1] if isinstance(tag, str) and "}" in tag else tag


# ════════════ Koodo Reader ════════════

def is_koodo_html(path: Path) -> bool:
    """判定一个 html 是否 Koodo 读书笔记：文件名含 koodoreader，或内容含「Koodo Reader - Highlights」完整标识（不区分大小写）。"""
    if path.suffix.lower() != ".html":
        return False
    if KOODO_NAME_MARKER in path.name.lower():
        return True
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return KOODO_CONTENT_MARKER in text.lower()


def parse_koodo_html(html_path: Path) -> dict:
    """解析一个 Koodo Reader highlight html，返回 {书名: {author, notes:[{chapter,text,date}]}}。"""
    root = etree.fromstring(html_path.read_bytes(), parser=etree.HTMLParser(encoding="utf-8"))
    # 书名 = 第一个 h2（h1 是固定标题「Koodo Reader - Highlights」）
    book_title = ""
    for el in root.iter():
        if _lname(el) == "h2":
            book_title = "".join(el.itertext()).strip()
            break
    if not book_title:
        # 取消「空壳」判定：解析不到 h2 书名时，用文件名兜底，照常处理（不再整文件跳过）
        book_title = html_path.stem
    if not book_title:
        return {}
    # 作者 = 任意 <em>（Koodo 导出里作者是 <p><em>余秋雨</em></p>）
    author = ""
    for el in root.iter():
        if _lname(el) == "em":
            t = "".join(el.itertext()).strip()
            if t:
                author = t
                break
    # 遍历所有 h3（章节）+ 紧随的 blockquote（原文）+ meta（日期）
    notes = []
    for el in root.iter():
        if _lname(el) != "h3":
            continue
        chapter = "".join(el.itertext()).strip()
        cur = el.getnext()
        while cur is not None:
            tag = _lname(cur)
            if tag == "h3":
                break
            if tag == "blockquote":
                text = "".join(cur.itertext()).strip()
                if text:
                    notes.append({"chapter": chapter, "text": text, "date": ""})
            if tag == "p" and cur.get("class") == "meta":
                # meta 格式：Date: 2026-10-15  |  Color: #xxx
                m = re.search(r"Date:\s*([\d-]+)", "".join(cur.itertext()) or "")
                if m and notes and notes[-1]["date"] == "":
                    notes[-1]["date"] = m.group(1)
            cur = cur.getnext()
    return {book_title: {"author": author, "notes": notes}}


def build_koodo_xhtml(book: str, data: dict) -> str:
    """生成 Koodo 合并 xhtml（书名 h1 → 章节 h2 → 笔记 blockquote + meta）。"""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<html xmlns="http://www.w3.org/1999/xhtml">',
        '<head><meta charset="utf-8"/><title>%s</title>' % book,
        '<style>',
        'body { font-family: Georgia, serif; max-width: 860px; margin: 40px auto; padding: 0 20px; color: #222; line-height: 1.7; }',
        'h1 { font-size: 1.8em; border-bottom: 2px solid #444; padding-bottom: 8px; }',
        'h2 { font-size: 1.3em; margin-top: 1.6em; color: #333; }',
        'blockquote { border-left: 4px solid #aaa; margin: 0.6em 0; padding: 6px 16px; background: #f9f9f9; color: #444; }',
        'blockquote.dup { border-left-color: #e8a33d; background: #fff7e6; }',
        '.meta { font-size: 0.85em; color: #888; margin: 0 0 1em; }',
        '.dup-flag { font-size: 0.8em; color: #c8841a; margin: -0.5em 0 1em; }',
        '</style></head><body>',
        '<h1>%s</h1>' % book,
    ]
    if data.get("author"):
        lines.append('<p class="author">%s</p>' % _html.escape(data["author"]))
    # 按章节分组（保持出现顺序）
    chapters: dict[str, list] = {}
    order: list[str] = []
    for n in data.get("notes", []):
        ch = n["chapter"]
        if ch not in chapters:
            chapters[ch] = []
            order.append(ch)
        chapters[ch].append(n)
    for ch in order:
        lines.append('<h2>%s</h2>' % _html.escape(ch))
        for n in chapters[ch]:
            cls = ' class="dup"' if n.get("dup") else ""
            lines.append('<blockquote%s>%s</blockquote>' % (cls, _html.escape(n["text"])))
            lines.append('<p class="meta">%s</p>' % _html.escape(n["date"] or ""))
            if n.get("dup"):
                lines.append('<p class="dup-flag">⚠ 疑似部分重复，待核对</p>')
    lines.append('</body></html>')
    return "\n".join(lines)


def _parse_koodo_xhtml(xhtml_path: Path) -> dict:
    """把现有 <书名>.xhtml 反解成 {书名: {author, notes:[{chapter,text,date}]}}，作为多轮合并的「旧来源」。

    源 html 已不保留（陈少 2026-10-16 定：源不要了，xhtml 是唯一存储），
    所以再次投同一本书的新 Koodo html 时，旧笔记从这里反解出来与新笔记合并。
    """
    if not xhtml_path.exists():
        return {}
    root = etree.fromstring(xhtml_path.read_bytes(), parser=etree.HTMLParser(encoding="utf-8"))
    book = ""
    author = ""
    for el in root.iter():
        t = _lname(el)
        if t == "h1" and not book:
            book = "".join(el.itertext()).strip()
        if t == "p" and el.get("class") == "author" and not author:
            author = "".join(el.itertext()).strip()
    if not book:
        book = xhtml_path.stem
    notes = []
    chapter = ""
    for el in root.iter():
        t = _lname(el)
        if t == "h2":
            chapter = "".join(el.itertext()).strip()
        elif t == "blockquote":
            text = "".join(el.itertext()).strip()
            if text:
                notes.append({"chapter": chapter, "text": text, "date": ""})
        elif t == "p" and el.get("class") == "meta":
            m = re.search(r"^([\d-]+)", "".join(el.itertext()).strip())
            if m and notes and notes[-1]["date"] == "":
                notes[-1]["date"] = m.group(1)
    return {book: {"author": author, "notes": notes}}


def _process_koodo_file(html_path: Path, move: bool = True) -> Path | None:
    """处理单个 Koodo html：解析 → 与现有 xhtml 反解合并 → 写回 xhtml，源 html 进废纸篓不留。

    陈少 2026-10-16 定：Koodo 源 html 不保留（删了就删了），<书名>.xhtml 是唯一存储，
    后续「按不同类型 XHTML 再合并汇总」也基于它。多轮合并 = 现有 xhtml 反解旧笔记 + 新 html 笔记。
    """
    if not is_koodo_html(html_path):
        print(f"跳过（非 Koodo 笔记）: {html_path.name}")
        return None
    parsed = parse_koodo_html(html_path)
    if not parsed:
        # 极端兜底：连文件名都为空才走到这；用文件名兜底照常生成空笔记 xhtml（不判空壳）
        print(f"  警告：{html_path.name} 解析不到任何内容，用文件名兜底生成空笔记 xhtml")
        book_title = html_path.stem or html_path.name
        parsed = {book_title: {"author": "", "notes": []}}
    book = list(parsed.keys())[0]
    # 建书文件夹
    book_dir = KOODO_ROOT / book
    book_dir.mkdir(parents=True, exist_ok=True)
    xhtml_path = book_dir / (book + ".xhtml")
    # 合并来源：现有 xhtml 反解（旧笔记）+ 当前新 html；源 html 消费后进废纸篓不留
    old = _parse_koodo_xhtml(xhtml_path)
    merged = merge_books([old, parsed])
    # 源 html 不归档，直接进废纸篓（move=False 时保留原地，供 rebuild 场景）
    if move and html_path.exists() and html_path.parent != book_dir:
        _trash(html_path)
        print(f"  源 html 已进废纸篓（不保留）：{html_path.name}")
    # 遍历 merged 全部书名、每本落一个 xhtml
    written = 0
    for b, bdata in merged.items():
        bdir = KOODO_ROOT / b
        bdir.mkdir(parents=True, exist_ok=True)
        xp = bdir / (b + ".xhtml")
        xp.write_text(build_koodo_xhtml(b, bdata), encoding="utf-8")
        written += 1
        print(f"  生成 xhtml → {bdir.name}/{xp.name}（{len(bdata.get('notes', []))} 条笔记）")
        _report("notes", b, "ok", "%d 条笔记" % len(bdata.get("notes", [])), "Koodo")
    return xhtml_path


def _rebuild_koodo() -> int:
    """重合并 Koodo Reader 读书笔记/ 下所有书文件夹（一键重生成 xhtml）。

    源 html 已不保留（陈少 2026-10-16 定），所以 rebuild 从现有 xhtml 反解重写（幂等自检），
    不再依赖源 html。
    """
    if not KOODO_ROOT.is_dir():
        print("Koodo 读书笔记目录不存在:", KOODO_ROOT)
        return 1
    count = 0
    for book_dir in sorted(KOODO_ROOT.iterdir()):
        if not book_dir.is_dir() or book_dir.name == "_原始":
            continue
        xhtmls = sorted(book_dir.glob("*.xhtml"))
        if not xhtmls:
            continue
        for xp in xhtmls:
            data = _parse_koodo_xhtml(xp)
            for book, bdata in data.items():
                if book and bdata.get("notes"):
                    xp.write_text(build_koodo_xhtml(book, bdata), encoding="utf-8")
                    _report("notes", book, "ok",
                            "%d 条笔记" % len(bdata.get("notes", [])), "Koodo")
            count += 1
    print(f"Koodo 已重合并 {count} 本书。")
    return 0


# ════════════ Kindle 剪贴 ════════════

def is_kindle_txt(path: Path) -> bool:
    if path.suffix.lower() != ".txt":
        return False
    return CLIPPING_MARKER in path.name.lower()


def parse_clippings(txt_path: Path) -> dict:
    """解析 Kindle 剪贴 txt，返回 {书名: {author, notes:[{chapter,text,date}]}}。"""
    # utf-8-sig 自动剥掉文件头 BOM（Kindle 导出的 txt 常带 \ufeff）
    raw = txt_path.read_text(encoding="utf-8-sig", errors="ignore")
    # 按分隔行（连续等号）切块
    blocks = re.split(r"^={2,}\s*$", raw, flags=re.MULTILINE)
    result: dict[str, dict] = {}
    for blk in blocks:
        lines = [ln.rstrip() for ln in blk.splitlines()]
        # 去掉首尾空行
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue
        # 第一行：书名 (作者)
        title_line = lines[0].lstrip("\ufeff")
        author = ""
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", title_line)
        if m:
            book = m.group(1).strip()
            author = m.group(2).strip()
        else:
            book = title_line.strip()
        if not book:
            # 取消「空壳」判定：无书名块用文件名兜底，照常归入（不再整块跳过）
            book = txt_path.stem
        if not book:
            continue
        # 日期：从以 - 开头的元数据行提取「添加于 ...」
        date = ""
        for ln in lines[1:]:
            if ln.lstrip().startswith("-"):
                dm = re.search(r"添加于\s*(.+)$", ln)
                if dm:
                    date = dm.group(1).strip()
                break
        # 内容：剩余非元数据行
        content_lines = []
        for ln in lines[1:]:
            if ln.lstrip().startswith("-"):
                continue
            if ln.strip():
                content_lines.append(ln.strip())
        text = "\n".join(content_lines).strip()
        if not text:
            continue
        if book not in result:
            result[book] = {"author": author, "notes": []}
        elif not result[book]["author"] and author:
            result[book]["author"] = author
        result[book]["notes"].append({"chapter": "", "text": text, "date": date})
    return result


def build_kindle_xhtml(book: str, data: dict) -> str:
    """生成 Kindle 合并 xhtml（无章节，书名 h1 + 笔记 blockquote + meta 平铺）。"""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<html xmlns="http://www.w3.org/1999/xhtml">',
        '<head><meta charset="utf-8"/><title>%s</title>' % book,
        '<style>',
        'body { font-family: Georgia, serif; max-width: 860px; margin: 40px auto; padding: 0 20px; color: #222; line-height: 1.7; }',
        'h1 { font-size: 1.8em; border-bottom: 2px solid #444; padding-bottom: 8px; }',
        'blockquote { border-left: 4px solid #aaa; margin: 0.6em 0; padding: 6px 16px; background: #f9f9f9; color: #444; }',
        'blockquote.dup { border-left-color: #e8a33d; background: #fff7e6; }',
        '.meta { font-size: 0.85em; color: #888; margin: 0 0 1em; }',
        '.dup-flag { font-size: 0.8em; color: #c8841a; margin: -0.5em 0 1em; }',
        '</style></head><body>',
        '<h1>%s</h1>' % book,
    ]
    if data.get("author"):
        lines.append('<p class="author">%s</p>' % _html.escape(data["author"]))
    for n in data.get("notes", []):
        cls = ' class="dup"' if n.get("dup") else ""
        lines.append('<blockquote%s>%s</blockquote>' % (cls, _html.escape(n["text"])))
        lines.append('<p class="meta">%s</p>' % _html.escape(n["date"] or ""))
        if n.get("dup"):
            lines.append('<p class="dup-flag">⚠ 疑似部分重复，待核对</p>')
    lines.append('</body></html>')
    return "\n".join(lines)


def _process_kindle_file(txt_path: Path, move: bool = True) -> int | None:
    """处理单个 Kindle 剪贴 txt：解析 → 按书名分组 → 留底 → 生成各书 xhtml（幂等）。"""
    if not is_kindle_txt(txt_path):
        print(f"跳过（非 Kindle 剪贴）: {txt_path.name}")
        return None
    parsed = parse_clippings(txt_path)
    if not parsed:
        print(f"解析不到笔记: {txt_path.name}")
        return None
    # 原始 txt 留底到 _原始/（同名：相同丢弃 / 不同递增重命名，绝不留在投递箱）
    origin = KINDLE_ROOT / ORIGIN_DIRNAME
    if move:
        _archive_source(txt_path, origin, txt_path.name)
    # 幂等：每次重读 _原始 所有 txt 重新合并，生成全部书的 xhtml
    all_txt = sorted(origin.glob("*.txt"))
    parsed_all = [parse_clippings(t) for t in all_txt if is_kindle_txt(t)]
    merged = merge_books(parsed_all)
    n_books = 0
    for book, data in merged.items():
        safe = _safe_name(book)
        book_dir = KINDLE_ROOT / safe
        book_dir.mkdir(parents=True, exist_ok=True)
        xpath = book_dir / (safe + ".xhtml")
        xpath.write_text(build_kindle_xhtml(book, data), encoding="utf-8")
        n_books += 1
        print(f"  生成 xhtml → {safe}/{xpath.name}（{len(data.get('notes', []))} 条笔记）")
        _report("notes", book, "ok", "%d 条笔记" % len(data.get("notes", [])), "Kindle")
    return n_books


def _rebuild_kindle() -> int:
    """重合并 _原始 下所有剪贴，重生成所有书 xhtml。"""
    origin = KINDLE_ROOT / ORIGIN_DIRNAME
    if not origin.is_dir():
        print("没有 _原始 剪贴可重建。")
        return 0
    all_txt = sorted(t for t in origin.glob("*.txt") if is_kindle_txt(t))
    if not all_txt:
        print("没有 _原始 剪贴可重建。")
        return 0
    parsed_all = [parse_clippings(t) for t in all_txt]
    merged = merge_books(parsed_all)
    n = 0
    for book, data in merged.items():
        safe = _safe_name(book)
        book_dir = KINDLE_ROOT / safe
        book_dir.mkdir(parents=True, exist_ok=True)
        xpath = book_dir / (safe + ".xhtml")
        xpath.write_text(build_kindle_xhtml(book, data), encoding="utf-8")
        n += 1
        _report("notes", book, "ok", "%d 条笔记" % len(data.get("notes", [])), "Kindle")
    print(f"Kindle 已重合并 {n} 本书。")
    return 0


# ════════════ 统一入口 ════════════

def _route_file(path: Path, move: bool = True):
    """按文件自动识别 Koodo / Kindle 并处理。"""
    if is_koodo_html(path):
        return _process_koodo_file(path, move=move)
    if is_kindle_txt(path):
        return _process_kindle_file(path, move=move)
    print(f"跳过（无法识别的读书笔记文件）: {path.name}")
    return None


def _scan_inbox(inbox: Path, only: str | None = None):
    """扫投递箱，自动分流 Koodo/Kindle 并处理。only='koodo'|'kindle'|None。"""
    if not inbox.is_dir():
        print("投递箱不存在:", inbox)
        return
    koodo_files = sorted(f for f in inbox.glob("*.html") if is_koodo_html(f))
    kindle_files = sorted(f for f in inbox.glob("*.txt") if is_kindle_txt(f))
    print(f"发现 {len(koodo_files)} 个 Koodo html，{len(kindle_files)} 个 Kindle txt")
    if only in (None, "koodo"):
        for f in koodo_files:
            print(f"\n[Koodo] 处理: {f.name}")
            _process_koodo_file(f)
    if only in (None, "kindle"):
        for f in kindle_files:
            print(f"\n[Kindle] 处理: {f.name}")
            _process_kindle_file(f)


def _truncate(s: str, limit: int = 120) -> str:
    """报告里把长笔记截断，避免报告过长。"""
    s = (s or "").replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def _report_key(book: str, ta: str, tb: str) -> str:
    """报告条目稳定键（文本规范化+排序，与方向无关），用 md5 保证可跨轮次持久。"""
    return hashlib.md5(("\x1f".join((book, min(ta, tb), max(ta, tb)))).encode("utf-8")).hexdigest()


def _load_dup_state() -> dict:
    """读已勾选（已核对）条目状态：{key: True}。"""
    if not DUP_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(DUP_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_dup_report() -> int:
    """把本次跑出来的部分重复命中落盘到 _部分重复_待核对.md；无命中则清理旧报告。

    合并式：核对状态单独存 `_部分重复_核对状态.json`（key=md5），再次运行只重写报告、
    保留已勾选状态，不丢人工核对进度。返回命中条数。
    """
    if not DUP_FINDINGS:
        if DUP_REPORT_PATH.exists():
            DUP_REPORT_PATH.unlink()
            print("（无部分重复，已清理旧报告）")
        return 0
    checked = _load_dup_state()
    by_book: dict = {}
    for f in DUP_FINDINGS.values():
        by_book.setdefault(f["book"], []).append(f)
    lines = [
        "# 读书笔记 · 部分重复待核对",
        "",
        "生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
        "判定：一条笔记是另一条的子串（互相包含），或两条相似度 ≥ %.2f。" % PARTIAL_DUP_RATIO,
        "以下笔记对疑似部分重复，请人工核对取舍（xhtml 中已用橙色标出）。",
        "核对后把该组 `[ ]` 改为 `[x]`（两条一起勾）；再次运行保留已勾选状态。",
        "",
    ]
    for book in sorted(by_book):
        items = by_book[book]
        lines.append("## %s" % book)
        if items[0].get("author"):
            lines.append("作者：%s" % items[0]["author"])
        for it in items:
            a, b = it["a"], it["b"]
            key = _report_key(book, _norm_text(a.get("text", "")), _norm_text(b.get("text", "")))
            mark = "x" if key in checked else " "
            lines.append("")
            lines.append("- [%s] 笔记A（%s）：%s" % (mark, a.get("date", "") or "无日期", _truncate(a.get("text", ""))))
            lines.append("- [%s] 笔记B（%s）：%s" % (mark, b.get("date", "") or "无日期", _truncate(b.get("text", ""))))
        lines.append("")
    DUP_REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print("⚠ 部分重复 %d 处（%d 本书，已核对 %d），见：%s"
          % (len(DUP_FINDINGS), len(by_book), len(checked), DUP_REPORT_PATH.name))
    return len(DUP_FINDINGS)


def main():
    ap = argparse.ArgumentParser(description="读书笔记合并（Koodo Reader + Kindle）")
    ap.add_argument("--inbox", type=str, nargs="?", const=str(INBOX),
                    help="投递箱路径（不填默认 %s）" % INBOX)
    ap.add_argument("--file", type=str, help="处理单个文件（自动识别 Koodo/Kindle）")
    ap.add_argument("--rebuild", action="store_true",
                    help="重合并 Koodo + Kindle 全部笔记")
    ap.add_argument("--koodo", action="store_true",
                    help="只处理 Koodo 部分（配合 --inbox / --rebuild）")
    ap.add_argument("--kindle", action="store_true",
                    help="只处理 Kindle 部分（配合 --inbox / --rebuild）")
    args = ap.parse_args()

    DUP_FINDINGS.clear()  # 每轮跑前清空，避免跨调用累积

    if args.rebuild:
        if not args.kindle:
            _rebuild_koodo()
        if not args.koodo:
            _rebuild_kindle()
        write_dup_report()
        return 0

    if args.file:
        _route_file(Path(args.file))
        write_dup_report()
        return 0

    inbox = Path(args.inbox) if args.inbox else INBOX
    only = "koodo" if args.koodo else ("kindle" if args.kindle else None)
    _scan_inbox(inbox, only=only)
    write_dup_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
