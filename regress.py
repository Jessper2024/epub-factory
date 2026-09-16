#!/usr/bin/env python3
"""回归测试：把每本 EPUB 的关键数字存成基线，改规则后一比就知道有没有改坏。

    ./epub.sh test          比对基线（篇数/月份/目录条目/体积/源篇数）
    ./epub.sh test --save   用当前状态重存基线（确认改动是有意的之后）
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_epub as B  # noqa: E402

BASE_FILE = Path(__file__).resolve().parent / "baselines.json"
SIZE_TOLERANCE = 0.25          # 体积允许 ±25%（图片重压、封面变化会有浮动）


def _git_rev() -> str:
    """当前代码版本，写进基线留痕，方便回溯"这版基线是在哪个代码状态下存的"。"""
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parent),
                               "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "?"


def snapshot() -> dict:
    """当前每本书的可核对数字。"""
    data = {}
    for book in B.collect_book_dirs():
        _, _, src_count = B.book_span(book)
        epubs = sorted(book.glob("*.epub"))
        for ep in epubs:
            st = B.epub_stats(ep)
            st.pop("error", None)
            st["source_xhtml"] = src_count
            data[ep.name] = st
        if not epubs:
            data["%s（未成书）" % book.name] = {"source_xhtml": src_count}
    return data


def compare(base: dict, cur: dict) -> list[str]:
    problems = []
    for name in sorted(set(base) | set(cur)):
        if name.startswith("_"):        # _meta 是留痕信息，不是书
            continue
        if name not in cur:
            problems.append("%s 不见了" % name)
            continue
        if name not in base:
            problems.append("%s 是新书（跑 ./epub.sh test --save 记录）" % name)
            continue
        b, c = base[name], cur[name]
        for key in ("months", "articles", "toc", "source_xhtml"):
            if b.get(key) != c.get(key):
                problems.append("%s  %s：%s → %s（基线 %s）"
                                % (name, key, b.get(key), c.get(key), b.get(key)))
        if "size_mb" in b and "size_mb" in c:
            lo, hi = b["size_mb"] * (1 - SIZE_TOLERANCE), b["size_mb"] * (1 + SIZE_TOLERANCE)
            if not lo <= c["size_mb"] <= hi:
                problems.append("%s  体积 %.1fMB 超出基线 %.1fMB ±%d%%"
                                % (name, c["size_mb"], b["size_mb"], SIZE_TOLERANCE * 100))
    return problems


def main() -> int:
    save = "--save" in sys.argv
    cur = snapshot()
    if save:
        payload = {
            "_meta": {"updated_at": dt.datetime.now().isoformat(timespec="seconds"),
                      "git": _git_rev(), "books": len(cur)},
            **cur,
        }
        BASE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        try:
            from core import audit
            audit.log("baseline.save", books=len(cur),
                      articles=sum(v.get("articles", 0) for v in cur.values()),
                      git=_git_rev())
        except Exception:
            pass
        print("基线已更新 →", BASE_FILE)
        for name, st in cur.items():
            print("  %-46s %s" % (name, st))
        return 0
    if not BASE_FILE.exists():
        print("还没有基线，先跑：./epub.sh test --save")
        return 1
    base = json.loads(BASE_FILE.read_text(encoding="utf-8"))
    meta = base.get("_meta") or {}
    if meta.get("updated_at"):
        print("基线存于 %s（代码 %s）" % (meta["updated_at"], meta.get("git", "?")))
    problems = compare(base, cur)
    if problems:
        print("✗ 与基线不一致（%d 处）" % len(problems))
        for p in problems:
            print("  -", p)
        print("\n如果这次改动是有意的，跑 ./epub.sh test --save 重存基线。")
        return 1
    print("✓ 与基线一致（%d 本）" % len(cur))
    for name, st in cur.items():
        print("   %-46s 篇 %s · 月 %s · 目录 %s · %.1fMB"
              % (name, st.get("articles", "-"), st.get("months", "-"),
                 st.get("toc", "-"), st.get("size_mb", 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
