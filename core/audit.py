#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构化审计日志：每一次运行都留下可机读的证据。

为什么要有这一层
    长期系统最怕"不知道它干了什么"。处理报告是给人看的散文，审计日志是给机器看的流水：
    什么时候跑了什么命令、耗时多久、新增/跳过/失败各多少、当时代码是什么版本。
    出问题时能回答"最近一次正常是什么时候""这次改动之后有没有异常"。

存储
    <项目根>/_审计/YYYY-MM.jsonl     一行一条 JSON（append-only，天然按时间序）
    <项目根>/_审计/current.jsonl     软链/副本，指向当月，方便 tail

用法
    from core import audit

    audit.log("inbox.start", dry_run=True)

    with audit.task("inbox", dry_run=True) as t:          # 自动计时、自动记录结果
        ...干活...
        t.stats(added=5, skipped=2, failed=0)
        # 出错时：t.fail("某篇转换失败")   会标记 status=failed

    audit.recent(20)                                       # 最近 20 条
    audit.recent_failures(7)                               # 最近 7 天失败
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

from . import config

_LEVELS = ("debug", "info", "warn", "error")


def audit_dir() -> Path:
    d = config.get("audit_dir")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_for(ts: dt.datetime | None = None) -> Path:
    ts = ts or dt.datetime.now()
    return audit_dir() / f"{ts:%Y-%m}.jsonl"


def _git_head() -> str:
    """当前代码版本（短 commit）。拿不到就返回 unknown——审计不能因为 git 异常而中断业务。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(config.engine_dir()), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def log(event: str, level: str = "info", **fields) -> dict:
    """写一条审计记录。任何异常都不能影响主流程（审计是旁挂，不是关键路径）。"""
    rec = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "level": level if level in _LEVELS else "info",
        "git": _git_head(),
    }
    rec.update(fields)
    try:
        p = _path_for()
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


class task:
    """上下文管理器：自动计时并在退出时写一条完成/失败记录。

    with audit.task("inbox", dry_run=True) as t:
        t.stats(added=5)
    # 正常退出 -> status=ok ；抛异常 -> status=error 且异常继续往上抛
    """

    def __init__(self, name: str, **fields):
        self.name = name
        self.fields = dict(fields)
        self._extra: dict = {}
        self._failed: str | None = None
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.time()
        log(f"{self.name}.start", **self.fields)
        return self

    def stats(self, **kw) -> None:
        """补充结果数字（added / skipped / failed / held …）。"""
        self._extra.update(kw)

    def fail(self, reason: str) -> None:
        """标记本次为失败（不抛异常，让调用方自己决定要不要继续）。"""
        self._failed = reason

    def __exit__(self, exc_type, exc, tb):
        dur = round(time.time() - self._t0, 2)
        if exc_type is not None:
            status, level, err = "error", "error", f"{exc_type.__name__}: {exc}"
        elif self._failed:
            status, level, err = "failed", "warn", self._failed
        else:
            status, level, err = "ok", "info", None
        rec = {"status": status, "duration_s": dur}
        rec.update(self._extra)
        if err:
            rec["error"] = err
        log(f"{self.name}.end", level=level, **{**self.fields, **rec})
        return False   # 不吞异常


# ---------------------------------------------------------------- 查询
def _iter_records(days: int | None = None, path: Path | None = None):
    if path:
        files = [path]
    else:
        files = sorted(audit_dir().glob("*.jsonl"), reverse=True)
    cutoff = None
    if days:
        cutoff = dt.datetime.now() - dt.timedelta(days=days)
    for f in files:
        if not f.exists():
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if cutoff:
                try:
                    if dt.datetime.fromisoformat(rec["ts"]) < cutoff:
                        continue
                except Exception:
                    pass
            yield rec


def recent(n: int = 20, event: str | None = None) -> list[dict]:
    """最近 n 条记录（新→旧）。可只筛某个 event。"""
    out = []
    for rec in _iter_records():
        if event and not str(rec.get("event", "")).startswith(event):
            continue
        out.append(rec)
        if len(out) >= n:
            break
    return out


def recent_failures(days: int = 7) -> list[dict]:
    """最近 N 天的失败/错误记录。看板用它显示"最近有没有出问题"。"""
    return [
        r for r in _iter_records(days=days)
        if r.get("level") in ("warn", "error") or r.get("status") in ("failed", "error")
    ]


def summary(days: int = 7) -> dict:
    """最近 N 天的运行概览：跑了多少次、失败几次、各命令耗时。"""
    runs, fails = 0, 0
    by_cmd: dict[str, dict] = {}
    for rec in _iter_records(days=days):
        ev = str(rec.get("event", ""))
        if not ev.endswith(".end"):
            continue
        runs += 1
        cmd = ev[:-4]
        d = by_cmd.setdefault(cmd, {"runs": 0, "failed": 0, "total_s": 0.0})
        d["runs"] += 1
        d["total_s"] += float(rec.get("duration_s") or 0)
        if rec.get("status") in ("failed", "error"):
            d["failed"] += 1
            fails += 1
    for d in by_cmd.values():
        d["total_s"] = round(d["total_s"], 1)
    return {"days": days, "runs": runs, "failures": fails, "by_command": by_cmd}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "summary":
        print(json.dumps(summary(), ensure_ascii=False, indent=2))
    elif cmd == "recent":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        for r in recent(n):
            print(f"{r['ts']}  {r['event']:24} {r.get('status', '')} {r.get('duration_s', '')}")
    elif cmd == "failures":
        fs = recent_failures()
        print(f"最近失败 {len(fs)} 条")
        for r in fs[:10]:
            print(f"  {r['ts']}  {r['event']}  {r.get('error', '')}")
    else:
        print("用法: python3 -m core.audit {summary|recent [n]|failures}")
