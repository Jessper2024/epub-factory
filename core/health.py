#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康检查：系统能不能自己说"我现在正常吗"。

为什么要有这一层
    长期无人值守的系统，最危险的不是报错，是**静默失效**——定时任务悄悄不跑了、
    磁盘满了、某个依赖没了、备份三个月没做。等到发现时数据已经丢了。
    preflight 把"能不能干活"的前提条件逐项检查一遍，出问题直接摆到看板上。

检查项（每项 ok / warn / fail）
    deps      关键依赖能不能 import（lxml / Pillow / requests）
    disk      项目所在磁盘剩余空间
    dirs      关键目录是否存在且可写
    git       代码工作区状态（有多少未提交改动）
    backup    最新备份在几天内（没有备份 -> warn）
    failures  最近 7 天审计里的失败/错误次数
    sources   每个号是否都有 xhtml 源（缺源就无法重建）
    watch     源料文件夹是否存在（不存在则收文必定空转）

评分
    100 分起，warn 扣 8、fail 扣 25，最低 0。>=90 健康，>=70 注意，否则告警。

用法
    from core import health
    health.check_all()        # -> dict
    print(health.report_text())
    python3 -m core.health    # 命令行体检
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from . import audit, config, safety

# 磁盘告警阈值（字节）
DISK_WARN_BYTES = 2 * 1024 ** 3      # 2 GB
DISK_FAIL_BYTES = 500 * 1024 ** 2    # 500 MB
# 备份年龄告警阈值（天）
BACKUP_WARN_DAYS = 7


def _check_deps() -> tuple[str, bool, str]:
    missing = []
    for mod in ("lxml", "PIL", "requests"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if not missing:
        return "ok", True, "lxml / Pillow / requests 均可用"
    return "fail", False, "缺少依赖：" + "、".join(missing)


def _check_disk() -> tuple[str, bool, str]:
    free = safety.disk_free_bytes(config.root())
    if free < 0:
        return "warn", True, "查不到磁盘剩余空间"
    gb = free / 1024 ** 3
    if free < DISK_FAIL_BYTES:
        return "fail", False, f"磁盘仅剩 {gb:.1f}GB，写盘随时可能失败"
    if free < DISK_WARN_BYTES:
        return "warn", True, f"磁盘剩 {gb:.1f}GB，建议清理"
    return "ok", True, f"磁盘剩余 {gb:.1f}GB"


def _check_dirs() -> tuple[str, bool, str]:
    root = config.root()
    need = [root, config.engine_dir(), Path(config.get("inbox_dir"))]
    bad = []
    for d in need:
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write-probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except Exception as e:
            bad.append(f"{d.name}({type(e).__name__})")
    if bad:
        return "fail", False, "不可写：" + "、".join(bad)
    return "ok", True, "项目根 / 代码目录 / 收件箱均可写"


def _check_git() -> tuple[str, bool, str]:
    try:
        import subprocess
        out = subprocess.run(
            ["git", "-C", str(config.engine_dir()), "status", "--porcelain"],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode != 0:
            return "warn", True, "git 状态读取失败（可能不是仓库）"
        n = len([l for l in out.stdout.splitlines() if l.strip()])
        if n == 0:
            return "ok", True, "代码工作区干净"
        return "warn", True, f"有 {n} 处未提交改动（改动没进版本库就不可回滚）"
    except Exception as e:
        return "warn", True, f"git 检查跳过：{type(e).__name__}"


def _find_latest_backup() -> Path | None:
    """找最新一份备份。位置与命名都从 config 读（与 backup.sh 同一真相源）——
    两边各写一份就会"明明打了备份、体检却说没有"。"""
    d = Path(config.get("backup_dir")).expanduser()
    prefix = config.get("backup_prefix") or ""
    cands = []
    if d.is_dir():
        cands = list(d.glob(f"{prefix}*.tar.gz"))
    if not cands:
        # 兼容：老备份可能直接在项目根下
        cands = list(config.root().glob(f"{prefix}*.tar.gz"))
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def _check_backup() -> tuple[str, bool, str]:
    p = _find_latest_backup()
    if p is None:
        return "warn", True, "没有任何备份（不可再生资产目前只有一份）"
    age = (dt.datetime.now() - dt.datetime.fromtimestamp(p.stat().st_mtime)).days
    if age > BACKUP_WARN_DAYS:
        return "warn", True, f"最新备份 {age} 天前（{p.name}）"
    return "ok", True, f"最新备份 {age} 天前（{p.name}）"


def _check_failures() -> tuple[str, bool, str]:
    try:
        fs = audit.recent_failures(days=7)
    except Exception:
        return "ok", True, "审计不可用"
    if not fs:
        return "ok", True, "最近 7 天无失败记录"
    latest = fs[0]
    return "warn", True, (
        f"最近 7 天 {len(fs)} 次异常；最近一次 {latest.get('ts','?')} "
        f"{latest.get('event','')} {latest.get('error','')}"
    )


def _book_dirs() -> list[Path]:
    """顶层下真正的公众号目录。判定口径必须与 build_epub.collect_book_dirs 完全一致：
    排除 `.`/`_` 开头、skip_dirs、名字含 backup_hint。两处口径不一致就会出现
    "主脚本认、体检不认（或反之）"的错位。"""
    root = config.root()
    skip = set(config.get("skip_dirs") or [])
    hint = config.get("backup_hint") or ""
    out = []
    try:
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith((".", "_")):
                continue
            if d.name in skip or (hint and hint in d.name):
                continue
            out.append(d)
    except Exception:
        pass
    return out


def _source_dir(book: Path) -> Path:
    """与 build_epub.source_dir 同款兼容：优先 xhtml/，老结构平铺则用号目录本身。"""
    sub = book / config.get("xhtml_subdir")
    return sub if sub.is_dir() else book


def _check_sources() -> tuple[str, bool, str]:
    """每个号是否都有 xhtml 源（没有源就无法随规则升级重建，也无法重新出书）。"""
    books = _book_dirs()
    if not books:
        return "warn", True, "还没有任何号"
    missing = []
    for b in books:
        sd = _source_dir(b)
        try:
            if not any(sd.glob("*.xhtml")):
                missing.append(b.name)
        except Exception:
            missing.append(b.name)
    if missing:
        return "fail", False, f"{len(missing)} 个号缺 xhtml 源：" + "、".join(missing)
    return "ok", True, f"{len(books)} 个号全部有源"


def _check_structure() -> tuple[str, bool, str]:
    """结构规范性：源必须收在 <号>/xhtml/ 里，不能平铺在号目录根下。

    平铺能被主流程兼容（source_dir 会退回号目录），所以不会立刻出错，
    但会让"号目录里既有源又有成品又有封面"混成一团，后续任何按结构扫描
    的新功能都可能误伤。属于"现在没坏、将来必坏"的隐患，所以单列一项。"""
    books = _book_dirs()
    flat = []
    for b in books:
        sub = b / config.get("xhtml_subdir")
        if not sub.is_dir():
            try:
                if any(b.glob("*.xhtml")):
                    flat.append(b.name)
            except Exception:
                pass
    if flat:
        return "warn", True, (
            f"{len(flat)} 个号的源平铺在号目录根下（应为 <号>/xhtml/）：" + "、".join(flat)
            + "。当前靠兼容逻辑能构建，建议规整。"
        )
    return "ok", True, f"{len(books)} 个号目录结构规范"


def _check_watch() -> tuple[str, bool, str]:
    w = config.watch_dir()
    if not w.exists():
        return "fail", False, f"源料文件夹不存在：{w}（收文会空转）"
    try:
        n = sum(1 for p in w.rglob("*")
                if p.is_file() and p.suffix.lower() in (".html", ".htm"))
    except Exception:
        n = -1
    return "ok", True, f"源料文件夹就绪，现有 {n} 个 html" if n >= 0 else "源料文件夹就绪"


_CHECKS = (
    ("deps", "依赖", _check_deps),
    ("disk", "磁盘", _check_disk),
    ("dirs", "目录可写", _check_dirs),
    ("git", "代码版本", _check_git),
    ("backup", "备份", _check_backup),
    ("failures", "近期异常", _check_failures),
    ("sources", "号源完整", _check_sources),
    ("structure", "目录规范", _check_structure),
    ("watch", "源料文件夹", _check_watch),
)


def check_all() -> dict:
    """跑全部检查，返回 {score, level, checks:[...]}。单项异常不影响其他项。"""
    items = []
    score = 100
    for key, label, fn in _CHECKS:
        try:
            status, ok, detail = fn()
        except Exception as e:      # 检查本身炸了不能拖垮体检
            status, ok, detail = "warn", True, f"检查异常：{type(e).__name__}: {e}"
        if status == "fail":
            score -= 25
        elif status == "warn":
            score -= 8
        items.append({"key": key, "label": label, "status": status,
                      "ok": ok, "detail": detail})
    score = max(0, min(100, score))
    level = "healthy" if score >= 90 else ("attention" if score >= 70 else "alert")
    return {
        "score": score,
        "level": level,
        "level_text": {"healthy": "健康", "attention": "需注意", "alert": "告警"}[level],
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        "checks": items,
    }


def report_text() -> str:
    """人类可读的体检报告。"""
    r = check_all()
    lines = [f"系统健康：{r['score']} 分（{r['level_text']}）  {r['checked_at']}", ""]
    icon = {"ok": "✓", "warn": "!", "fail": "✗"}
    for c in r["checks"]:
        lines.append(f"  [{icon.get(c['status'], '?')}] {c['label']:10} {c['detail']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        import json
        print(json.dumps(check_all(), ensure_ascii=False, indent=2))
    else:
        print(report_text())
