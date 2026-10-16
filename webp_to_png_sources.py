#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量回改源 xhtml：把内联的 data:image/webp 就地转成 data:image/png。

为什么（陈少 2026-10-17 定）：
    PageEdit / macOS 快速查看等预览工具**不支持 base64 内联的 WebP**，
    会把图显示成 `@image#1` 占位符，正文图全看不见。微信图床现在默认返回 WebP，
    历史源 xhtml 里攒了一大批 WebP。转换器已改为「转换期 WebP→PNG」，
    本脚本一次性把**存量**历史文件补齐（幂等：已是 PNG 的跳过）。

用法：
    python3 webp_to_png_sources.py --dry-run     # 预演，只报不改
    python3 webp_to_png_sources.py               # 正式回改
"""
from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_epub as B          # noqa: E402  借用 collect_book_dirs / source_dir

# 匹配内联 webp：data:image/webp;base64,<data>
_WEBP_RE = re.compile(r'data:image/webp;base64,([A-Za-z0-9+/=]+)')


def _webp_to_png_b64(b64: str) -> str | None:
    """base64 webp → base64 png；失败返回 None（原样保留）。"""
    try:
        from PIL import Image
        import io
        payload = base64.b64decode(b64)
        img = Image.open(io.BytesIO(payload))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:      # noqa: BLE001
        return None


def process_file(p: Path, dry: bool) -> tuple[int, int, bool]:
    """返回 (转成功数, 失败数, 是否写盘)。"""
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as exc:                       # noqa: BLE001
        print("  ✗ 读取失败，跳过：%s -> %s" % (p.name, exc))
        return (0, 0, False)
    if "data:image/webp" not in text:
        return (0, 0, False)                       # 幂等：没有 webp 直接跳过

    ok = fail = 0

    def _sub(m: "re.Match[str]") -> str:
        nonlocal ok, fail
        new_b64 = _webp_to_png_b64(m.group(1))
        if new_b64 is None:
            fail += 1
            return m.group(0)                      # 转不了就原样留着
        ok += 1
        return "data:image/png;base64," + new_b64

    new_text = _WEBP_RE.sub(_sub, text)
    wrote = False
    if ok and not dry:
        p.write_text(new_text, encoding="utf-8")
        wrote = True
    return (ok, fail, wrote)


def main() -> int:
    ap = argparse.ArgumentParser(description="全量回改源 xhtml：内联 WebP → PNG")
    ap.add_argument("--dry-run", action="store_true", help="只报不改，一个文件都不动")
    args = ap.parse_args()

    files = changed = done = failed = 0
    for d in B.collect_book_dirs():
        xd = B.source_dir(d)
        for p in sorted(xd.rglob("*.xhtml")):
            files += 1
            ok, fail, _wrote = process_file(p, args.dry_run)
            if ok or fail:
                changed += 1
                done += ok
                failed += fail
                print("  %s  webp→png %d%s%s" % (
                    p.name[:50], ok, "  失败 %d" % fail if fail else "",
                    "  （预演）" if args.dry_run else ""))
    print("=" * 60)
    print("扫描 %d 个源 xhtml → 需改 %d 个；转 PNG %d 张，失败 %d 张%s"
          % (files, changed, done, failed,
             "（预演，未落盘）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
