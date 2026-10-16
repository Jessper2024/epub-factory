#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史重排：把各号 xhtml/ 下平铺的文稿按「年+月」归到月份子目录。

陈少 2026-10-16 定：XHTML 按「年+月」分子目录分类，所有号统一。
本脚本只做「把平铺的 *.xhtml 移到 xhtml/YYYY年M月/」这一件事：
- 幂等：已在月份子目录里的不动；无日期前缀的留在平铺层。
- 不碰 epub、不碰 html/ 原始留底、不删任何文件。
- 只处理 source_dir 返回目录的**平铺层**（不含已存在的子目录）。

用法：
    python3 reorg_month_dirs.py --dry-run    # 只报要移什么
    python3 reorg_month_dirs.py              # 实际移动
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_epub as B  # noqa: E402

_MONTH_RE = re.compile(r"^(\d{4})年(\d{1,2})月")


def month_key(name: str) -> str:
    m = _MONTH_RE.match(name)
    if not m:
        return ""
    return f"{m.group(1)}年{int(m.group(2))}月"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    moved = 0
    for d in B.collect_book_dirs():
        sd = B.source_dir(d)
        flat = [p for p in sd.glob("*.xhtml") if p.is_file()]
        for p in sorted(flat):
            key = month_key(p.name)
            if not key:
                continue
            sub = sd / key
            target = sub / p.name
            if target == p:
                continue
            if args.dry_run:
                print(f"  [预演] {d.name}/{p.name} → {key}/")
                moved += 1
            else:
                sub.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(target))
                moved += 1
                print(f"  ✓ {d.name}/{p.name} → {key}/")

    print("\n" + ("（预演）共需移动 %d 篇" % moved if args.dry_run
                  else "完成：移动 %d 篇" % moved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
