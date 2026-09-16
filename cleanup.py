#!/usr/bin/env python3
"""空间清理：把"该删没删"的中间产物清到废纸篓。

    ./epub.sh cleanup            预演：只列清单，一个文件都不动（默认）
    ./epub.sh cleanup --apply    真删（走系统废纸篓，可恢复，绝不硬删）

分三类：
  中间产物   图片提取的调试产物、废弃方案的抓取数据与试验品 epub
  旧版成品   被新书取代的旧成品（**只有在外接盘 旧版epub/ 里有副本时才删**）
  已归档源料 源料文件夹里已经被收进某号的 html（用判重指纹判定，跟收件箱同一套逻辑）

不可触碰（写死在 PROTECTED 里，任何情况都不删）：
  各号 xhtml/ · 原始HTML/ · 成品 epub · cover.jpg · _待确认新号/ · _engine 代码 · 指纹表
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))

from core import config as C                      # noqa: E402
import build_epub as B                            # noqa: E402

# 这些路径/后缀任何情况下都不删——删了就是不可逆的资产损失
PROTECTED_PARTS = ("xhtml", "原始HTML", "_待确认新号", "core", ".git", ".venv")
PROTECTED_SUFFIX = (".py", ".sh", ".md", ".json", ".command")


def human(n: float) -> str:
    return "%.0fMB" % (n / 1048576) if n >= 1048576 else "%.0fKB" % (n / 1024)


def size_of(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def is_protected(p: Path) -> bool:
    parts = set(p.parts)
    if parts & set(PROTECTED_PARTS):
        return True
    if p.suffix.lower() in PROTECTED_SUFFIX:
        return True
    # 号目录下的成品 epub 与封面是资产，不是中间产物
    if p.suffix.lower() == ".epub" and p.parent.parent == ROOT:
        return True
    if p.name == "cover.jpg":
        return True
    return False


# ── 三类待清理 ────────────────────────────────────────────────
def find_intermediate() -> list[Path]:
    """中间过程产物：图片提取的调试产物 + 废弃方案 B 的抓取数据与试验品。"""
    out: list[Path] = []
    pic = ROOT / "_图片解析"
    if pic.is_dir():
        out.append(pic)
    crawl = ENGINE / "yuanchuan_crawler"
    # 只删废弃方案的抓取数据与解出的图片；里面的成品 epub 是当时真做出来的书，留着
    for name in ("raw_html", "epub_images"):
        p = crawl / name
        if p.exists():
            out.append(p)
    return out


def find_old_epub() -> tuple[list[Path], list[str]]:
    """被新书取代的旧成品。外接盘 旧版epub/ 里没副本的一律不删。"""
    dest = Path(C.get("backup_dir")).expanduser()
    mirror_old = dest / "旧版epub"
    # 必须递归：外接盘里是 旧版epub/<原目录名>/<epub>，只扫顶层会误判"没有副本"
    have = {p.name for p in mirror_old.rglob("*.epub")} if mirror_old.is_dir() else set()
    out, notes = [], []
    for d in sorted(ROOT.glob("_旧版备份_*")):
        if not d.is_dir():
            continue
        epubs = [p for p in d.rglob("*.epub")]
        missing = [p.name for p in epubs if p.name not in have]
        if not dest.is_dir():
            notes.append("%s：备份盘不可用，跳过（不删无副本的东西）" % d.name)
            continue
        if missing:
            notes.append("%s：%s 在外接盘没有副本，跳过" % (d.name, "、".join(missing)))
            continue
        out.append(d)
    return out, notes


def find_archived_sources() -> tuple[list[Path], list[Path]]:
    """源料文件夹：判重指纹命中已归档的才删，命中不了的（未处理/改名）一律留着。"""
    src = Path(C.get("watch_dir")).expanduser()
    if not src.is_dir():
        return [], []
    idx = B.fingerprint_index(dry_run=True)
    hit, miss = [], []
    for p in sorted(src.iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".html", ".htm"):
            continue
        keys = B.content_fingerprints(p)
        (hit if any(k in idx for k in keys) else miss).append(p)
    return hit, miss


# ── 执行 ──────────────────────────────────────────────────────
def to_trash(paths: list[Path]) -> int:
    """走系统废纸篓（/usr/bin/trash），可恢复。

    小批量（10 个一批）+ 批后验证：一旦某批没删掉就立刻停下来报告，
    绝不"以为删完了其实没删"，也绝不一个命令扫掉几十个文件。
    """
    done = 0
    for i in range(0, len(paths), 10):
        batch = paths[i:i + 10]
        r = subprocess.run(["/usr/bin/trash", *[str(p) for p in batch]],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  ✗ 第 %d 批失败，停止：%s" % (i // 10 + 1, r.stderr.strip()[:200]))
            return done
        still = [p for p in batch if p.exists()]
        if still:
            print("  ✗ 第 %d 批有 %d 个没删掉，停止" % (i // 10 + 1, len(still)))
            for p in still[:5]:
                print("     ", p)
            return done
        done += len(batch)
        print("  · 已清 %d/%d" % (done, len(paths)))
    return done


def main() -> int:
    apply = "--apply" in sys.argv
    print("空间清理%s　%s" % ("（执行）" if apply else "（预演，不动文件）",
                              dt.datetime.now().strftime("%Y-%m-%d %H:%M")))

    groups: list[tuple[str, list[Path], list[str]]] = []
    inter = find_intermediate()
    groups.append(("中间产物", inter, [str(p.relative_to(ROOT)) for p in inter]))
    old, notes = find_old_epub()
    groups.append(("旧版成品（外接盘有副本）", old,
                   [str(p.relative_to(ROOT)) for p in old] + notes))
    hit, miss = find_archived_sources()
    groups.append(("已归档源料", hit,
                   ["%d 个已归档（指纹命中），另有 %d 个未归档一律保留"
                    % (len(hit), len(miss))] if hit else []))

    total = 0
    allp: list[Path] = []
    for title, paths, detail in groups:
        mb = sum(size_of(p) for p in paths)
        total += mb
        allp.extend(paths)
        print("\n【%s】 %d 项 · %s" % (title, len(paths), human(mb)))
        for line in detail[:12]:
            print("   ", line)
        if len(detail) > 12:
            print("    … 另 %d 项" % (len(detail) - 12))

    blocked = [p for p in allp if is_protected(p)]
    if blocked:
        print("\n✗ 有 %d 项命中保护清单，拒绝清理：" % len(blocked))
        for p in blocked[:10]:
            print("   ", p)
        return 1

    print("\n合计可回收：%s（%d 项）" % (human(total), len(allp)))
    if not apply:
        print("预演结束。确认无误后跑：./epub.sh cleanup --apply")
        return 0
    if not allp:
        print("没有要清理的东西。")
        return 0

    n = to_trash(allp)
    print("已移入废纸篓：%d/%d 项（可恢复：废纸篓里右键「放回原处」）" % (n, len(allp)))
    try:
        from core import audit
        audit.log("cleanup", files=n, mb=round(total / 1048576, 1))
    except Exception:
        pass
    return 0 if n == len(allp) else 1


if __name__ == "__main__":
    raise SystemExit(main())
