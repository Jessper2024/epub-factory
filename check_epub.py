#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合订 EPUB 交付前体检：源 xhtml + 产物 epub 一起验。

2026-09-17 修两个 bug（都是真事故，别退回去）：

A. **假阴性**：原来 `if not (book/"xhtml").is_dir(): continue` ——源平铺在号目录根下的书
   会被整本跳过，连 epub 都不查。晚点LatePost 因此一直显示"0 问题"，
   实际是根本没被扫描（把源规整进 xhtml/ 后才暴露出 5 处误报）。
   现在：没有源要**明说**，epub 该查照查——体检绝不能因为结构特殊就静默放行。

B. **阈值误用**：40 字（max_heading_len）是【文内小标题 h3】的判定阈值，
   原来被拿去卡【文章标题】，导致 42~45 字的正常文章标题被误报"超长标题"。
   现在文章标题用 article_title_max（80），小标题仍用 40。
   判定口径与 build_epub 保持一致：文章标题（源 h1 / 合订 h2）不被降级，所以不该被卡。
"""
import sys
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import config  # noqa: E402

XHTML_NS = "http://www.w3.org/1999/xhtml"
ROOT = config.root()

SUBTITLE_MAX = config.get("max_heading_len")        # 文内小标题 h3 / 月份 h1
ARTICLE_MAX = config.get("article_title_max")       # 文章标题（源 h1 / 合订 h2）


def lname(el):
    t = el.tag
    return t.split("}")[-1] if isinstance(t, str) else ""


def txt(el):
    return "".join(el.itertext()).strip()


def _source_dir(book: Path) -> Path:
    """与 build_epub.source_dir 同款：优先 xhtml/，老结构平铺则用号目录本身。"""
    sub = book / config.get("xhtml_subdir")
    return sub if sub.is_dir() else book


def check_sources(book):
    """返回 (问题列表, 是否有源)。"""
    src = _source_dir(book)
    bad = []
    files = sorted(src.glob("*.xhtml"))
    if not files:
        return [(book.name, "没有 xhtml 源（无法重建，也无法随规则升级）")], False

    for p in files:
        try:
            tree = etree.parse(str(p), etree.XMLParser(resolve_entities=False, no_network=True))
        except Exception as e:
            bad.append((p.name, "XML 解析失败: %s" % e))
            continue
        body = tree.getroot().find(f"{{{XHTML_NS}}}body")
        if body is None:
            bad.append((p.name, "无 body"))
            continue
        h1s = [el for el in body.iter() if lname(el) == "h1"]
        h2s = [el for el in body.iter() if lname(el) == "h2"]
        # 文章标题 h1 恰好一个且是首个块；小标题统一 h3
        if len(h1s) != 1:
            bad.append((p.name, "h1 数量 = %d（应为 1）" % len(h1s)))
        if h2s:
            bad.append((p.name, "残留 h2 %d 个" % len(h2s)))
        for el in body.iter():
            tag = lname(el)
            if tag not in ("h1", "h2", "h3"):
                continue
            t = txt(el)
            limit = ARTICLE_MAX if tag == "h1" else SUBTITLE_MAX   # h1=文章标题，h3=小标题
            if not t:
                bad.append((p.name, "空标题 <%s>" % tag))
            elif len(t) > limit:
                bad.append((p.name, "%s 超长(%d>%d) %s"
                            % ("文章标题" if tag == "h1" else "小标题", len(t), limit, t[:24])))
            elif t.startswith("["):
                bad.append((p.name, "引用条目当标题 %s" % t[:24]))
    return bad, True


def check_epub(epub):
    issues = []
    ext = 0
    empty_h = 0
    entries = 0
    with zipfile.ZipFile(epub) as z:
        for n in z.namelist():
            if not n.lower().endswith((".xhtml", ".html", ".ncx", ".opf")):
                continue
            try:
                data = z.read(n)
            except Exception as e:
                issues.append("%s 读取失败 %s" % (n, e))
                continue
            try:
                root = etree.fromstring(data)
            except Exception as e:
                issues.append("%s XML 失败: %s" % (n, e))
                continue
            if "/Text/part" in n:
                entries += 1
            for el in root.iter():
                tag = lname(el)
                if tag in ("h1", "h2", "h3"):
                    t = txt(el)
                    # h1=月份（短），h2=文章标题（可较长），h3=小标题
                    limit = ARTICLE_MAX if tag == "h2" else SUBTITLE_MAX
                    if not t:
                        empty_h += 1
                    elif len(t) > limit or t.startswith("["):
                        issues.append("%s 异常标题(%d>%d) %s" % (n, len(t), limit, t[:24]))
                if tag == "img":
                    s = el.get("src", "")
                    if s.startswith(("http://", "https://")):
                        ext += 1
    return issues, ext, empty_h, entries


def _book_dirs():
    skip = set(config.get("skip_dirs") or [])
    hint = config.get("backup_hint") or ""
    out = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        if d.name in skip or (hint and hint in d.name):
            continue
        out.append(d)
    return out


def main():
    if sys.argv[1:]:
        targets = [ROOT / n for n in sys.argv[1:]]
    else:
        targets = _book_dirs()

    total_bad = 0
    for book in targets:
        if not book.is_dir():
            continue
        print("=" * 60)
        print("【%s】" % book.name)
        bad, has_src = check_sources(book)
        if bad:
            total_bad += len(bad)
            for f, why in bad[:20]:
                print("  源 xhtml ✗", f[:36], "→", why)
        else:
            print("  源 xhtml ✓ 标题规范")

        epubs = sorted(book.glob("*.epub"))
        if not epubs:
            print("  无 epub 产物" + ("（且无源：这本书无法重建）" if not has_src else ""))
            continue
        for ep in epubs:
            issues, ext, empty_h, entries = check_epub(ep)
            print("  %s：月份 %d 个 · 外链图 %d · 空标题 %d"
                  % (ep.name, entries, ext, empty_h))
            if issues:
                total_bad += len(issues)
                for i in issues[:20]:
                    print("    ✗", i)
            else:
                print("    ✓ XML 全部可解析、无异常标题")
            if ext:
                print("    ✗ 有 %d 张外链图" % ext)
            else:
                print("    ✓ 图片全部内联")
            if empty_h:
                print("    ✗ 有 %d 个空标题" % empty_h)
    print("=" * 60)
    print("问题总数：", total_bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
