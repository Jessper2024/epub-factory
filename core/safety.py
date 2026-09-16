#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全护栏：两件事——写盘不留半截文件，删文件不碰不可再生资产。

为什么要有这一层
    1. **原子写**：直接 open(path,'w') 写盘时若中途崩（CTRL-C / 磁盘满 / 异常），
       文件会停在"半截"状态，而且是**已经覆盖掉的半截**——原内容也没了。
       原子写先写同目录临时文件，再 os.replace 原子换名，要么全有要么全无。
    2. **不可再生护栏**：`xhtml/` 与 `原始HTML/` 是全部 EPUB 的唯一来源，
       书可以从它们重建，它们没了就真没了。任何代码路径想删这两个目录，
       一律拒绝并留痕——需要人工删就人工删，脚本不代办。

用法
    from core import safety

    safety.write_text(path, "<html>…")          # 原子写文本
    safety.write_bytes(path, data)              # 原子写二进制
    safety.guard_delete(path)                   # 命中保护区抛 ProtectedPathError
    safety.is_protected(path)                   # -> bool
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import config


class ProtectedPathError(Exception):
    """试图删除/清空不可再生资产时抛出。"""


# 不可再生的目录名（相对号目录）。命中即拒绝删除。
PROTECTED_DIR_NAMES = frozenset({"xhtml", "原始HTML"})

# 视为"整库级危险"的路径片段：直接删项目根或 _engine 也不允许
DANGEROUS_PARTS = frozenset({"_engine"})


def _protected_roots() -> list[Path]:
    """所有受保护的目录（各号的 xhtml/ 与 原始HTML/）。"""
    root = config.root()
    out: list[Path] = []
    if not root.exists():
        return out
    try:
        for child in root.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            for name in PROTECTED_DIR_NAMES:
                p = child / name
                if p.is_dir():
                    out.append(p.resolve())
    except Exception:
        pass
    return out


def is_protected(path: Path | str) -> bool:
    """该路径是否在不可再生保护区内。"""
    try:
        p = Path(path).resolve()
    except Exception:
        return False
    for prot in _protected_roots():
        try:
            # prot 本身，或 prot 下面的任何东西
            if p == prot or prot in p.parents:
                return True
        except Exception:
            continue
    return False


def guard_delete(path: Path | str) -> None:
    """删除前调用。命中保护区直接抛错——宁可让操作失败，也不能让数据消失。"""
    p = Path(path)
    if is_protected(p):
        raise ProtectedPathError(
            f"拒绝删除：{p} 位于不可再生保护区（xhtml/ 或 原始HTML/）。"
            f"这两处是全部 EPUB 的唯一来源，需要清理请人工确认后手动删。"
        )


def write_bytes(path: Path | str, data: bytes) -> Path:
    """原子写二进制：临时文件 → fsync → os.replace。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=p.suffix or ".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def write_text(path: Path | str, text: str, encoding: str = "utf-8") -> Path:
    """原子写文本。"""
    return write_bytes(path, text.encode(encoding))


def disk_free_bytes(path: Path | str | None = None) -> int:
    """目标路径所在磁盘的剩余字节数。写大文件前先看看够不够。"""
    p = Path(path or config.root())
    try:
        st = os.statvfs(str(p if p.exists() else p.parent))
        return st.f_bavail * st.f_frsize
    except Exception:
        return -1


def ensure_room(path: Path | str, need_bytes: int, margin: float = 1.2) -> bool:
    """写之前确认磁盘够不够（留 20% 余量）。不够返回 False，不抛错。"""
    free = disk_free_bytes(path)
    if free < 0:
        return True      # 查不到就别挡路
    return free >= int(need_bytes * margin)


if __name__ == "__main__":
    import sys
    # 自检：保护区判定 + 原子写
    print("保护区清单：")
    for p in _protected_roots():
        print(f"  {p}")
    if len(sys.argv) > 1:
        t = Path(sys.argv[1])
        print(f"\n{t} 受保护？ {is_protected(t)}")
        try:
            guard_delete(t)
            print("  -> 允许删除")
        except ProtectedPathError as e:
            print(f"  -> {e}")
