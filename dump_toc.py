#!/usr/bin/env python3
"""导出 EPUB 的三级目录树，人工核对用。"""
import sys, zipfile
from lxml import etree

X = "http://www.w3.org/1999/xhtml"


def dump(ep):
    z = zipfile.ZipFile(ep)
    r = etree.fromstring(z.read("OEBPS/Text/nav.xhtml"))
    nav = r.find(".//{%s}nav" % X)
    n = [0]

    def walk(ol, depth):
        for li in ol.findall("{%s}li" % X):
            a = li.find("{%s}a" % X)
            sp = li.find("{%s}span" % X)
            node = a if a is not None else sp
            label = "".join(node.itertext()).strip() if node is not None else "(空)"
            n[0] += 1
            print("  " * depth + "- " + label)
            sub = li.find("{%s}ol" % X)
            if sub is not None:
                walk(sub, depth + 1)

    walk(nav.find("{%s}ol" % X), 0)
    print("总条目", n[0])


for p in sys.argv[1:]:
    print("###", p.rsplit("/", 1)[-1])
    dump(p)
