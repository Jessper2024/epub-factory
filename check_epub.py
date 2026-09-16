#!/usr/bin/env python3
"""合订 EPUB 交付前体检：源 xhtml + 产物 epub 一起验。"""
import sys, re, zipfile
from pathlib import Path
from lxml import etree

XHTML_NS = "http://www.w3.org/1999/xhtml"
ROOT = Path("/Users/jessper/Life/EPUB制作")


def lname(el):
    t = el.tag
    return t.split("}")[-1] if isinstance(t, str) else ""


def txt(el):
    return "".join(el.itertext()).strip()


def check_sources(book):
    src = book / "xhtml"
    bad = []
    for p in sorted(src.glob("*.xhtml")):
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
        # 文章标题 h1 应恰好一个且是首个块；小标题统一 h3
        if len(h1s) != 1:
            bad.append((p.name, "h1 数量 = %d（应为 1）" % len(h1s)))
        if h2s:
            bad.append((p.name, "残留 h2 %d 个" % len(h2s)))
        for el in body.iter():
            if lname(el) not in ("h1", "h2", "h3"):
                continue
            t = txt(el)
            if not t:
                bad.append((p.name, "空标题 <%s>" % lname(el)))
            elif len(t) > 40:
                bad.append((p.name, "超长标题(%d) %s" % (len(t), t[:24])))
            elif t.startswith("["):
                bad.append((p.name, "引用条目当标题 %s" % t[:24]))
    return bad


def check_epub(epub):
    issues = []
    ext = 0
    empty_h = 0
    entries = 0
    with zipfile.ZipFile(epub) as z:
        names = z.namelist()
        for n in names:
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
                if lname(el) in ("h1", "h2", "h3"):
                    t = txt(el)
                    if not t:
                        empty_h += 1
                    elif len(t) > 40 or t.startswith("["):
                        issues.append("%s 异常标题 %s" % (n, t[:24]))
                if lname(el) == "img":
                    s = el.get("src", "")
                    if s.startswith(("http://", "https://")):
                        ext += 1
            # 目录层级
    return issues, ext, empty_h, entries


def main():
    targets = sys.argv[1:] or [d.name for d in sorted(ROOT.iterdir())
                               if d.is_dir() and not d.name.startswith((".", "_"))
                               and d.name not in ("微信公众号下载", "EPUB成品")]
    total_bad = 0
    for name in targets:
        book = ROOT / name
        if not (book / "xhtml").is_dir():
            continue
        print("=" * 60)
        print("【%s】" % name)
        bad = check_sources(book)
        if bad:
            total_bad += len(bad)
            for f, why in bad[:20]:
                print("  源 xhtml ✗", f[:36], "→", why)
        else:
            print("  源 xhtml ✓ 标题规范")
        epubs = sorted(book.glob("*.epub"))
        if not epubs:
            print("  无 epub 产物")
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


if __name__ == "__main__":
    main()
