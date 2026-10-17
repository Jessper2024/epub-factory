#!/usr/bin/env python3
"""merge_numbered_headings.py —— 存量 xhtml「纯数字标题 + 真标题」合并回改。

陈少 2026-10-17 定（所有公众号生效）：微信/公众号导出里常见「编号独占一个标题、
下一行才是真标题」两行式小标题，在 EPUB 目录里变成两条。规则：

  - 纯数字标题（1-2 位）后紧跟真标题 h2/h3.section-title → 合并成「N、标题」，删数字标题。
  - 纯数字标题后紧跟 <hr/> 再跟真标题 → 同样合并，<hr/> 一并删。
  - 纯数字标题后直接是正文段落（九边/饭统，作者用数字分节、本无文字标题）→ 保留不动。

幂等：已是「N、标题」的不重复处理。支持 --dry-run。

用法：
  python3 merge_numbered_headings.py <根目录> [--dry-run]
"""
import argparse
import re
import sys
from pathlib import Path

from lxml import etree, html

XHTML_NS = "http://www.w3.org/1999/xhtml"
NUM_RE = re.compile(r"^\d{1,2}$")


def _local(tag: str) -> str:
    return tag.split('}')[-1].lower()


def _text(node) -> str:
    return "".join(node.itertext()).strip()


def _merge(blocks: list) -> tuple[list, int]:
    """合并纯数字标题 + 真标题。返回 (新块列表, 合并次数)。"""
    out = []
    merged_count = 0
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if _local(b.tag) not in ("h2", "h3") or b.get("class") != "section-title":
            out.append(b)
            i += 1
            continue
        text = _text(b)
        if not NUM_RE.match(text):
            out.append(b)
            i += 1
            continue
        # 纯数字标题：看下一个非空块
        j = i + 1
        # 跳过紧随的 <hr/>
        if j < n and _local(blocks[j].tag) == "hr":
            j += 1
        if j < n and _local(blocks[j].tag) in ("h2", "h3") and blocks[j].get("class") == "section-title":
            next_text = _text(blocks[j])
            if next_text and not NUM_RE.match(next_text):
                merged = blocks[j]
                merged.text = f"{text}、{merged.text or ''}"
                out.append(merged)
                merged_count += 1
                i = j + 1
                continue
        out.append(b)
        i += 1
    return out, merged_count


def process(path: Path, dry_run: bool) -> int:
    raw = path.read_text(encoding="utf-8")
    # xhtml 是 XML 格式（有 <?xml?> + <!DOCTYPE> + 自闭合标签），必须用 XML 解析器，
    # 否则 lxml 的 html parser 会把 <meta/> 等自闭合标签展开成不闭合的 <meta>，破坏 XML 合法性。
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True, recover=False)
    root = etree.fromstring(raw.encode("utf-8"), parser=parser)
    # body 带 xhtml namespace
    body = root.find(".//{http://www.w3.org/1999/xhtml}body")
    if body is None:
        body = root
    blocks = list(body)
    new_blocks, merged = _merge(blocks)
    if merged == 0:
        return 0
    if dry_run:
        print(f"  [dry-run] {path.name}: 合并 {merged} 处")
        return merged
    # 重建 body 子节点（就地替换兄弟序列）
    for child in list(body):
        body.remove(child)
    for b in new_blocks:
        body.append(b)
    # XML 序列化，保留 DOCTYPE 和 XML 声明（encoding 用 utf-8 bytes 才能带 xml_declaration）
    out_bytes = etree.tostring(root, encoding="utf-8", xml_declaration=True, doctype='<!DOCTYPE html>')
    path.write_bytes(out_bytes)
    print(f"  合并 {merged} 处 → {path.name}")
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="xhtml 根目录（递归扫 *.xhtml）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    files = sorted(root.rglob("*.xhtml"))
    total_merged = 0
    touched = 0
    for f in files:
        m = process(f, args.dry_run)
        if m:
            total_merged += m
            touched += 1
    print(f"完成：{touched} 个文件 / 合并 {total_merged} 处" + ("（dry-run）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
