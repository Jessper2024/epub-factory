#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集中配置：全项目常量的单一真相源。

为什么要有这一层
    改造前常量散落在 build_epub.py 各处（MIN_PUBLISH、WATCH_DIR、MAX_HEADING_LEN…），
    改一个阈值要在代码里翻找，而且容易改了这处忘了那处。现在集中在这里。

铁律
    **每个默认值都必须等于改造前代码里的硬编码值**。配置层是"搬家"不是"调参"。
    要改阈值，改这里或改 config.local.json，不要回到业务脚本里写死。

覆盖机制（优先级从低到高）
    1. 本文件的 DEFAULTS
    2. `config.local.json`（在 _engine/ 下，已被 .gitignore，不进版本库）
    3. 环境变量 `EPUB_<KEY 大写>`（如 EPUB_MIN_PUBLISH=10）

用法
    from core import config
    config.get("min_publish")        # -> 30
    config.get("watch_dir")          # -> Path(...)
    config.set("min_publish", 10)    # 只改内存，不落盘
    config.save_local()              # 把当前值写进 config.local.json（持久化）
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# core/ 在 _engine/ 下，所以：
#   core/config.py -> parent=core -> parent=_engine -> parent=项目根
CORE_DIR = Path(__file__).resolve().parent
ENGINE_DIR = CORE_DIR.parent
ROOT = ENGINE_DIR.parent

LOCAL_FILE = ENGINE_DIR / "config.local.json"

# ---------------------------------------------------------------- 默认值
# 全部等于改造前 build_epub.py 里的硬编码值，一个都不能随手改。
DEFAULTS: dict = {
    # —— 路径 ——
    "engine_dir": str(ENGINE_DIR),
    "root": str(ROOT),
    "watch_dir": "/Users/jessper/Life/01、源料_微信公众号下载",   # 源料文件夹（只盯这一个）
    "xhtml_subdir": "xhtml",          # 不可再生：EPUB 由它重建
    "raw_subdir": "原始HTML",          # 不可再生：转换前的原 HTML
    "cover_name": "cover.jpg",
    "inbox_dir": str(ROOT / "_待处理"),
    "pending_dir": str(ROOT / "_待确认新号"),
    "audit_dir": str(ROOT / "_审计"),

    # 顶层下"看起来像目录但并不是公众号"的名字（必须与 build_epub.SKIP_DIRS 一致）
    "skip_dirs": ["EPUB成品", ".workbuddy", "微信公众号下载"],
    "backup_hint": "旧版备份",      # 名字里含这个的一律不当号目录

    # —— 成书 ——
    "min_publish": 30,                # 首次成书门槛（篇）；0 = 关掉
    "scan_subdirs": 1,                # 源料文件夹往下扫几层
    "sort_author": "沪上陈少",          # 排序作者（恒定，不随号变）

    # —— 标题判定（normalize_headings 的唯一口径）——
    # 注意：max_heading_len 只管【文内小标题 h3】，不能拿去卡【文章标题】。
    # 文章标题天然会更长（如「独家专访蚂蚁 CEO 韩歆毅：我们已重回战场…」42 字），
    # 用 40 去卡会把正常文章标题误报成"超长标题"（2026-09-17 修：晚点LatePost 5 篇误报）。
    "max_heading_len": 40,            # 小标题（h3）上限
    "article_title_max": 80,          # 文章标题（源 h1 / 合订 h2）上限
    "max_heading_tail": 14,           # 序号后短语上限
    "caption_max_len": 20,            # 图后短块≤此长度视为图注，降级不进目录

    # —— 运行 ——
    "cloud_interval": 14400,          # 云端看板发布间隔（秒）= 4 小时
    "inbox_interval": 3600,           # 收文扫描间隔（秒）= 1 小时
    "dash_port": 8760,
    "dash_cache_ttl": 300,            # 看板扫描缓存（秒）

    # —— 备份（3-2-1）——
    # 备份位置与命名必须是单一真相源：backup.sh 按它写，health 按它找。
    # 两边各写一份就会"明明打了备份、体检却说没有"（2026-09-17 踩过）。
    "backup_dir": str(Path.home() / "Life" / "EPUB备份"),
    "backup_prefix": "EPUB源_",
    "backup_keep": 5,                 # 本地保留几份
    "backup_targets": [],             # 异地目标（第二/第三副本），留空=只本地
}

# 需要以 Path 形式返回的键
_PATH_KEYS = {"engine_dir", "root", "watch_dir", "inbox_dir", "pending_dir", "audit_dir"}
# 需要以 int 形式返回的键
_INT_KEYS = {
    "min_publish", "scan_subdirs", "max_heading_len", "max_heading_tail",
    "caption_max_len", "cloud_interval", "inbox_interval", "dash_port",
    "dash_cache_ttl", "backup_keep",
}


def _coerce(key: str, value):
    """把字符串值还原成该有的类型（JSON 与环境变量读进来都是字符串）。"""
    if key in _PATH_KEYS:
        return Path(str(value)).expanduser()
    if key in _INT_KEYS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return DEFAULTS.get(key)
    return value


def _load_local() -> dict:
    """读 config.local.json。文件不存在/损坏都返回空 dict——配置坏了不能让系统起不来。"""
    if not LOCAL_FILE.exists():
        return {}
    try:
        data = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _env_overrides() -> dict:
    """读 EPUB_<KEY> 环境变量。"""
    out = {}
    prefix = "EPUB_"
    for k in DEFAULTS:
        env_key = prefix + k.upper()
        if env_key in os.environ:
            out[k] = os.environ[env_key]
    return out


def as_dict() -> dict:
    """当前生效的完整配置（默认值 + local + 环境变量，已转类型）。"""
    merged = dict(DEFAULTS)
    merged.update(_load_local())
    merged.update(_env_overrides())
    return {k: _coerce(k, v) for k, v in merged.items()}


def get(key: str, default=None):
    """取一个配置项。键不存在返回 default（没给就返回 DEFAULTS 里的，再没有返回 None）。"""
    merged = dict(DEFAULTS)
    merged.update(_load_local())
    merged.update(_env_overrides())
    if key not in merged:
        return default
    return _coerce(key, merged[key])


def set(key: str, value) -> None:
    """只改内存（供单测与临时覆盖）。持久化请用 save_local()。"""
    os.environ["EPUB_" + key.upper()] = str(value)


def save_local(**kwargs) -> Path:
    """把给定键值写进 config.local.json（持久化，不进版本库）。返回文件路径。"""
    data = _load_local()
    for k, v in kwargs.items():
        data[k] = str(v) if isinstance(v, Path) else v
    LOCAL_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return LOCAL_FILE


def reset_local() -> None:
    """删掉本地覆盖，全部回到默认值。"""
    if LOCAL_FILE.exists():
        LOCAL_FILE.unlink()


# ---------------------------------------------------------------- 便捷属性
def watch_dir() -> Path:
    return get("watch_dir")


def root() -> Path:
    return get("root")


def engine_dir() -> Path:
    return get("engine_dir")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "show":
        for k, v in sorted(as_dict().items()):
            print(f"{k:20} = {v}")
    else:
        print("用法: python3 -m core.config show")
