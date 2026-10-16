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
PROGRESS_FILE = ROOT / "_管理" / "_进度.json"

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
    """一次 ingest 运行的进度。整份重写，原子替换。

    板块化（陈少 2026-10-16）：新增 `sections` 字段，按「业务板块」分组展示。
    每板块一个数组，各板块独立 key，多段一起跑时互不覆盖（整份 merge 写）。
    """

    # 板块定义（key, 标题, 产出说明）——顺序即页面展示顺序。
    # 按「产物」分业务板块；「文稿精校」置顶（陈少 2026-10-16 定，只服务视频文稿线二次精校）
    SECTIONS = [
        ("proofread", "文稿精校", "视频线二次精校"),
        ("video",   "视频文稿", "字幕 / 精校稿"),
        ("notes",   "读书笔记", "笔记 xhtml"),
        ("article", "文章合集", "公众号 epub"),
        ("book",    "出版图书", "完整书 epub"),
        ("epub",    "EPUB 拆解", "分章 xhtml"),
        ("adult",   "成人作品集", "演员 epub"),
        ("photo",   "写真集", "图集 epub"),
        ("finish",  "系统", ""),
    ]

    def __init__(self, title: str = "投递箱处理", dry_run: bool = False):
        self.data = {
            "title": title,
            "dry_run": dry_run,
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "updated_at": "",
            "finished": False,
            "items": [],          # 兼容旧：文稿线逐项（ingest 用）
            "books": [],          # 兼容旧：重建的 epub
            "disassembles": [],   # 兼容旧：epub 拆解
            "sections": {},       # 新：板块化 {key: {title, items:[{name,state,detail,target}]}}
        }

    # ── 板块上报（新） ──────────────────────────────────────
    @staticmethod
    def _title(key: str) -> str:
        for k, t, _ in Progress.SECTIONS:
            if k == key:
                return t
        return key

    def section_item(self, key: str, name: str, state: str,
                     detail: str = "", target: str = "") -> None:
        """往某个板块追加/更新一条进度。key 见 SECTIONS。"""
        sec = self.data["sections"].setdefault(
            key, {"title": self._title(key), "items": []})
        for it in sec["items"]:
            if it["name"] == name:
                it["state"] = state
                it["detail"] = detail
                it["target"] = target
                break
        else:
            sec["items"].append(
                {"name": name, "state": state, "detail": detail, "target": target})
        self._flush()

    def section_done(self, key: str, note: str = "") -> None:
        """标记某板块整体完成（附一句总结）。"""
        sec = self.data["sections"].setdefault(
            key, {"title": self._title(key), "items": []})
        sec["done"] = True
        if note:
            sec["note"] = note
        self._flush()

    def _ensure_sections(self) -> None:
        """把所有板块都建出来（没内容的也显示「本次无内容」）。"""
        for key, title, _desc in self.SECTIONS:
            self.data["sections"].setdefault(key, {"title": title, "items": []})

    # ── 内容操作（旧，兼容） ─────────────────────────────────
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

    def set_book(self, name: str, state: str = "built", detail: str = "") -> None:
        """记录一本重建的 epub（陈少 2026-10-14：进度页要能看到「更新 epub」阶段）。"""
        self.data["books"].append({"name": name, "state": state, "detail": detail})
        self._flush()

    def set_disasm(self, name: str, state: str = "done", detail: str = "") -> None:
        """记录一本 epub 的拆解进度（陈少 2026-10-14：进度页要能看到拆解线）。"""
        for item in self.data["disassembles"]:
            if item["name"] == name:
                item["state"] = state
                item["detail"] = detail
                break
        else:
            self.data["disassembles"].append(
                {"name": name, "state": state, "detail": detail})
        self._flush()

    def plan_disasm(self, names: list[str]) -> None:
        """拆解前先列清单（waiting），页面马上看到「有几本要拆」。"""
        self.data["disassembles"] = [
            {"name": n, "state": "waiting", "detail": ""} for n in names
        ]
        self._flush()

    def finish(self, note: str = "") -> None:
        self._ensure_sections()
        self.data["finished"] = True
        if note:
            self.data["note"] = note
        self._flush()

    # ── 落盘 ──────────────────────────────────────────────────
    def _flush(self) -> None:
        self.data["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        # 合并盘上已有的 sections（跨 skill 用 report() 写入的），避免本实例的
        # 内存快照把别的进程刚写的板块进度覆盖掉。
        try:
            on_disk = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
            disk_secs = on_disk.get("sections")
            if isinstance(disk_secs, dict) and disk_secs:
                merged = dict(disk_secs)
                merged.update(self.data.get("sections", {}))
                self.data["sections"] = merged
        except (OSError, json.JSONDecodeError):
            pass
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


def clear_stale_running() -> int:
    """清掉上次运行遗留的孤儿「处理中」：state=running → fail（上次被中断）。

    长任务（视频抓取等）被中途杀掉时，来不及把 running 改成终态，
    页面会永远转圈。每次 command 启动时先清一遍，页面不再有卡死的「处理中」。
    返回清理条数。
    """
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    n = 0
    for sec in (data.get("sections") or {}).values():
        for it in sec.get("items", []):
            if it.get("state") == "running":
                it["state"] = "fail"
                it["detail"] = "上次被中断"
                n += 1
    if n:
        data["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        tmp = PROGRESS_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, PROGRESS_FILE)
        except OSError:
            pass
    return n


def report(key: str, name: str, state: str, detail: str = "",
           target: str = "", title: str = "") -> None:
    """跨 skill 独立上报：读现有 _进度.json，往某板块追加/更新一条后整份写回。

    供其它 skill 脚本（视频/读书笔记/成人/写真）调用，无需持有 Progress 实例——
    它们不在 ingest 的进程里，各跑各的，只能「读盘-改-写回」。
    """
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {
            "title": title or "投递箱处理",
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "finished": False,
            "items": [], "books": [], "disassembles": [], "sections": {},
        }
    secs = data.setdefault("sections", {})
    sec = secs.setdefault(key, {"title": title or key, "items": []})
    for it in sec.setdefault("items", []):
        if it.get("name") == name:
            it["state"] = state
            it["detail"] = detail
            it["target"] = target
            break
    else:
        sec.setdefault("items", []).append(
            {"name": name, "state": state, "detail": detail, "target": target})
    data["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, PROGRESS_FILE)
    except OSError:
        pass


def clear_stuck() -> dict:
    """彻底清掉卡死/失败的任务条目（陈少 2026-10-16 定，进度页「跳过卡死任务」按钮调用）。

    区别于 `clear_stale_running()`（那只把 running 降级成 fail、还留在列表）：
    这里把 `running`（卡死）和 `fail`（失败）条目**直接从列表删除**，让进度页不再显示。
    - running：删记录，数据源还在，下轮 command 会重新进队列重试。
    - fail：删记录；坏源料由 command 的入料兜底处理（丢废纸篓 / 拉黑）。
    返回 {"cleared": n, "running": n1, "fail": n2}。
    """
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"cleared": 0, "running": 0, "fail": 0}
    n_run = n_fail = 0
    for sec in (data.get("sections") or {}).values():
        items = sec.get("items")
        if not isinstance(items, list):
            continue
        kept = []
        for it in items:
            if it.get("state") == "running":
                n_run += 1
                continue
            if it.get("state") == "fail":
                n_fail += 1
                continue
            kept.append(it)
        sec["items"] = kept
    if n_run or n_fail:
        data["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        tmp = PROGRESS_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, PROGRESS_FILE)
        except OSError:
            pass
    return {"cleared": n_run + n_fail, "running": n_run, "fail": n_fail}
