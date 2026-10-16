#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""epub 拆解进度写入：供 auto_pipeline.sh 每拆一本调用，更新 `_进度.json` 的 disassembles。

用法：
    write_disasm.py plan "书名1" "书名2" ...     拆解前列清单（waiting）
    write_disasm.py set  "书名" running "拆解中"  更新某本状态
    write_disasm.py set  "书名" done "3 章"

直接读写学习工厂的 `_进度.json`（与 progress.py 同源），原子替换，幂等。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path("/Users/jessper/Life/学习工厂/02、文稿成品线")
PROGRESS_FILE = ROOT / "_管理" / "_进度.json"


def _load() -> dict:
    try:
        d = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    if "disassembles" not in d:
        d["disassembles"] = []
    return d


def _save(d: dict) -> None:
    d["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PROGRESS_FILE)


def plan(names: list[str]) -> None:
    d = _load()
    d["disassembles"] = [{"name": n, "state": "waiting", "detail": ""} for n in names]
    _save(d)


def set_one(name: str, state: str, detail: str = "") -> None:
    d = _load()
    for item in d["disassembles"]:
        if item["name"] == name:
            item["state"] = state
            item["detail"] = detail
            break
    else:
        d["disassembles"].append({"name": name, "state": state, "detail": detail})
    _save(d)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("用法: write_disasm.py plan <名...> | set <名> <state> [detail]")
        return 2
    if args[0] == "plan":
        plan(args[1:])
    elif args[0] == "set":
        if len(args) < 3:
            print("set 需要 <名> <state> [detail]")
            return 2
        set_one(args[1], args[2], args[3] if len(args) > 3 else "")
    else:
        print("未知子命令:", args[0])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
