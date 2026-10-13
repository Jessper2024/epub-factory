#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时进度上报：把 ingest 的处理进展写进 `_进度.json`，供浏览器页面轮询。

为什么要独立一个文件而不是直接打到 stdout
    页面的刷新和 ingest 的执行要**解耦**：
      · 页面刷不刷新、开不开，都不影响处理
      · 中途关掉浏览器，处理照跑
      · 那个 JSON 本身就是一份可追溯的处理记录
    所以进度落盘，页面只读盘——谁都不等谁。

写入协议
    每个文件处理完（或失败/待判）就**重写**整个文件（不是追加）：
    文件小（最多几十项），整写最简单、不会读到半行残缺的 JSON。
    为了让页面能感知"还在跑"，每次写都更新 `updated_at`。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_FILE = ROOT / "_进度.json"

# 状态取值：waiting / running / ok / dup / unknown / fail / skip
STATE_LABEL = {
    "waiting": "等待",
    "running": "处理中",
    "ok": "已归档",
    "dup": "重复跳过",
    "unknown": "待判",
    "fail": "失败",
    "skip": "跳过",
}


class Progress:
    """一次 ingest 运行的进度。整份重写，原子替换。"""

    def __init__(self, title: str = "投递箱处理", dry_run: bool = False):
        self.data = {
            "title": title,
            "dry_run": dry_run,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "updated_at": "",
            "finished": False,
            "items": [],          # [{name, state, detail, target}]
        }

    # ── 内容操作 ──────────────────────────────────────────────
    def plan(self, names: list[str]) -> None:
        """开场先列出全部待处理，页面马上能看到"一共几个"。"""
        self.data["items"] = [
            {"name": n, "state": "waiting", "detail": "", "target": ""}
            for n in names
        ]
        self._flush()

    def set_state(self, name: str, state: str, detail: str = "",
                  target: str = "") -> None:
        """更新某个文件的状态。找不到就补一条（zip 解出的文件可能不在计划里）。"""
        for item in self.data["items"]:
            if item["name"] == name:
                item["state"] = state
                item["detail"] = detail
                item["target"] = target
                break
        else:
            self.data["items"].append(
                {"name": name, "state": state, "detail": detail, "target": target})
        self._flush()

    def finish(self, note: str = "") -> None:
        self.data["finished"] = True
        if note:
            self.data["note"] = note
        self._flush()

    # ── 落盘 ──────────────────────────────────────────────────
    def _flush(self) -> None:
        self.data["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        tmp = PROGRESS_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, PROGRESS_FILE)      # 原子替换，页面永远读不到半截
        except OSError:
            pass


def finished_recently(seconds: int = 30) -> bool:
    """上次运行是不是刚结束（页面据此决定要不要提示"已完成"）。"""
    try:
        d = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        if not d.get("finished"):
            return False
        t = dt.datetime.fromisoformat(d["updated_at"])
        return (dt.datetime.now() - t).total_seconds() < seconds
    except Exception:
        return False
