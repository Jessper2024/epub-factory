#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作者目录归一（陈少 2026-10-14 定，command 双击时自动跑）。

扫描所有平台的作者目录，把命中号名归并规则的散落目录，合并进目标目录：
- 精确别名（六镇 → 九边）
- 子串归并（作者名含 tony/xtony → xtony）

规则唯一实现 = build_epub.merge_account()。本脚本只做「扫描 + 移动 + 删除空目录」，
不碰内容、不碰 epub（移动后由 ingest 的下一步重建 epub）。

用法：
    python3 merge_accounts.py            # 全库扫描归一并重建受影响的 epub
    python3 merge_accounts.py --dry-run  # 只报要归并什么，不动
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platforms as P          # noqa: E402
from build_epub import merge_account  # noqa: E402


def scan_and_merge(dry_run: bool) -> list[str]:
    """扫描所有平台作者目录，归并命中规则的散落目录。返回受影响的号名列表。"""
    touched: list[str] = []
    for key, plat in P.PLATFORMS.items():
        root = P.platform_dir(key)
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            target_name = merge_account(d.name)
            if target_name == d.name:
                continue  # 不归并，跳过
            target = root / target_name
            if target == d:
                continue
            # 目标目录存在则合并，不存在则改名
            if dry_run:
                print("  [预演] %s/%s → %s" % (plat, d.name, target_name))
            else:
                target.mkdir(parents=True, exist_ok=True)
                moved = 0
                # 合并 xhtml / html 子目录
                for sub in ("xhtml", "html"):
                    src_sub = d / sub
                    if not src_sub.is_dir():
                        continue
                    dst_sub = target / sub
                    dst_sub.mkdir(parents=True, exist_ok=True)
                    for f in src_sub.iterdir():
                        if f.is_file():
                            shutil.move(str(f), str(dst_sub / f.name))
                            moved += 1
                # cover.jpg 若目标没有则搬过去
                for cover in ("cover.jpg", "cover.png"):
                    if (d / cover).exists() and not (target / cover).exists():
                        shutil.move(str(d / cover), str(target / cover))
                # 删空目录（含残留的旧 epub，由后续重建覆盖）
                if d.exists():
                    shutil.rmtree(d)
                print("  ✓ 归并 %s/%s → %s（%d 文件）" % (plat, d.name, target_name, moved))
            touched.append(target_name)
    return sorted(set(touched))


def clean_epubs(dry_run: bool) -> int:
    """清理旧名 epub：每个作者目录只保留「目录名」对应的 epub，其余旧名 epub 删除。

    场景：作者名归一后，旧作者名的 epub（如「逍遥XTony（恩师张雪峰）.epub」）
    会残留在归一后的目录里（xtony/），与正确命名的「xtony.epub」并存。
    这里按「文件名去掉 _YYYYMM 后缀 == 目录名」判定，对不上的旧 epub 走废纸篓。
    """
    removed = 0
    for key, plat in P.PLATFORMS.items():
        root = P.platform_dir(key)
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            dirname = d.name
            for ep in sorted(d.glob("*.epub")):
                stem = ep.stem
                # 合法名 = 目录名 或 目录名_YYYYMM / 目录名_YYYYMM-YYYYMM / 目录名_YYYY
                base = stem.split("_")[0]
                if base == dirname:
                    continue  # 正确命名，保留
                # 旧名 epub（作者名归并前的），删掉
                if dry_run:
                    print("  [预演] 删旧 epub: %s/%s" % (plat, ep.name))
                else:
                    _trash(ep)
                    print("  ✓ 删旧 epub: %s/%s" % (plat, ep.name))
                removed += 1
    return removed


def _trash(path: Path) -> None:
    """走废纸篓（可恢复），回退 ~/.Trash。"""
    try:
        import subprocess
        subprocess.run(["/usr/bin/trash", str(path)], check=True)
    except Exception:
        shutil.move(str(path), str(Path.home() / ".Trash" / path.name))


def main() -> int:
    ap = argparse.ArgumentParser(description="作者目录归一")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-rebuild", action="store_true", help="只归并，不重建 epub")
    args = ap.parse_args()

    touched = scan_and_merge(args.dry_run)
    removed = clean_epubs(args.dry_run)
    if args.dry_run:
        print("  [预演] 共删旧 epub %d 本。" % removed)
        return 0
    if not touched:
        print("  无需归并（目录已干净）。")
        return 0

    if args.no_rebuild:
        print("  归并完成（跳过重建，共 %d 个号）。" % len(touched))
        return 0

    # 重建受影响的号（逐号重建 epub）
    print("  重建 %d 个号的 epub…" % len(touched))
    for acc in touched:
        import subprocess
        r = subprocess.run([sys.executable, "build_epub.py", "--only", acc],
                           capture_output=True, text=True)
        print("    %s：%s" % (acc, "✓" if r.returncode == 0 else "✗ " + (r.stderr or r.stdout)[-120:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
