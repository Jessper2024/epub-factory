#!/usr/bin/env python3
"""推广图黑名单的自我迭代。

思路：作者自己的配图不会跨文章重复，而推广图（关注矩阵 / 设为★ / 商务合作）必然每篇都在，
且固定在文末。所以——

    每篇都记账 → 跨文章重复 + 位于文末 = 疑似推广图（自动候选）→ 人工确认后永久删除

出厂预置（PROMO_FILE_IDS）作为初始 confirmed 名单；之后靠 promo_blacklist.json 自己长大。
数据文件是纯文本，可进 git，也能手改。

    ./epub.sh ads              看当前黑名单与候选
    ./epub.sh ads --promote-all   把所有候选转正（确认后执行）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
DATA_FILE = ENGINE_DIR / "promo_blacklist.json"

# 出厂预置：均为「饭统戴老板」号固定推广图，2026-09-16 视觉逐一确认过
PRESET = {
    "OGh1hyMTnsJqoA09XyMaAedfACb1a3EiaJ1kNo4cFa3mYzKc": "关注我们·矩阵",
    "OGh1hyMTnsKt7D7NasHgHicUgX2RUiaZFWfYn0ia1Saic10X8vYaBiac": "关注我们·矩阵新版",
    "OGh1hyMTnsLgDlWiaG23YCzroQy9yrqX4dsbME38cKKGeJneyULBbGHZ": "商务合作+设为★",
    "6FudoaFVUAj7n9lItPA3hYGglic0RmFkO8QiasSicyRXbW4I58tQLBuc": "设为★不要错过+商务邮箱",
    "6FudoaFVUAiahXJGvdRGiaRQAKvyX8JcnG2ia3htaBcUqWC6xQvBM6zF": "关注我们·矩阵新版2",
    "6FudoaFVUAiahXJGvdRGiaRQAKvyX8JcnG8g0BqNJWuibaQUpTqN483X": "设为★不要错过·新版2",
    "9E1iaWb6waicA0ialEoiba6Ck5JBEpFoBgfDQrTB1ibUnjnIu6x7uCdy": "欢迎加入远川研究所",
    "9E1iaWb6waicB03rKF4wKZVeqQxT9niadvzXnn0dv8nNro2P6RYFPstT": "欢迎加入远川研究所·2",
}

FID_RE = re.compile(r"mmbiz\.qpic\.cn/[A-Za-z0-9_]+/([A-Za-z0-9]{20,})")
TAIL_BLOCKS = 4          # 位于 js_content 最后 4 个块内算「文末」
CANDIDATE_MIN = 2        # 至少跨 2 篇出现在文末才自动升为候选
KEY_LEN = 40             # 图片 URL 里编号的长度不稳定，统一取前 40 位作钥匙（子串匹配照样命中）


def _key(fid: str) -> str:
    return (fid or "")[:KEY_LEN]


def _load() -> dict:
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    # 归一：老记录里可能存在同一张图的多种长度写法，统一并到 40 位钥匙上
    merged: dict[str, dict] = {}
    for raw_key, rec in data.items():
        k = _key(raw_key)
        if k in merged:
            old = merged[k]
            old["count"] = old.get("count", 0) + rec.get("count", 0)
            old["tail"] = old.get("tail", 0) + rec.get("tail", 0)
            for b in rec.get("books", []):
                if b not in old.setdefault("books", []):
                    old["books"].append(b)
            old["confirmed"] = old.get("confirmed") or rec.get("confirmed", False)
            old["promoted"] = old.get("promoted") or rec.get("promoted", False)
            if not old.get("url"):
                old["url"] = rec.get("url", "")
            if not old.get("note"):
                old["note"] = rec.get("note", "")
        else:
            merged[k] = dict(rec)
    data = merged
    for fid, note in PRESET.items():
        k = _key(fid)
        rec = data.setdefault(k, {"count": 0, "tail": 0, "books": [], "url": ""})
        rec["confirmed"] = True
        if not rec.get("note"):
            rec["note"] = note
    return data


def _save(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")


def active_ids() -> set[str]:
    """当前要删除的推广图 fileid：预置 + 已确认的。"""
    return set(PRESET) | {fid for fid, r in _load().items() if r.get("confirmed")}


def scan_entries(tree) -> list[dict]:
    """从微信 HTML 里取出正文所有图片的 (fileid, 是否文末, url)。"""
    content = tree.xpath('//*[@id="js_content"]')
    if not content:
        return []
    content = content[0]
    blocks = list(content)
    tail_from = max(0, len(blocks) - TAIL_BLOCKS)
    out = []
    for i, block in enumerate(blocks):
        for img in block.xpath('.//*[local-name()="img"]'):
            url = img.get("data-src") or img.get("src") or ""
            m = FID_RE.search(url)
            if not m:
                continue
            out.append({"fid": _key(m.group(1)), "tail": i >= tail_from, "url": url})
    return out


def record(account: str, entries: list[dict]) -> list[str]:
    """记一笔：更新每张图的出现次数与号名，返回本次新升为候选的 fileid。"""
    if not entries:
        return []
    data = _load()
    seen = set()
    new_cands = []
    for e in entries:
        fid = e["fid"]
        if fid in seen:            # 同一篇里出现多次只算一次
            continue
        seen.add(fid)
        rec = data.setdefault(fid, {"count": 0, "tail": 0, "books": [],
                                    "url": e["url"], "confirmed": False, "note": ""})
        rec["count"] = rec.get("count", 0) + 1
        if e["tail"]:
            rec["tail"] = rec.get("tail", 0) + 1
        if account and account not in rec.get("books", []):
            rec.setdefault("books", []).append(account)
        if not rec.get("url"):
            rec["url"] = e["url"]
        if (not rec.get("confirmed") and not rec.get("promoted")
                and rec.get("tail", 0) >= CANDIDATE_MIN):
            rec["promoted"] = True      # 只在升为候选时报一次
            new_cands.append(fid)
    _save(data)
    return new_cands


def candidates() -> dict[str, dict]:
    """还没确认、也没被否掉、但已经像推广图的（文末重复出现）。"""
    return {fid: r for fid, r in _load().items()
            if not r.get("confirmed") and not r.get("rejected")
            and r.get("tail", 0) >= CANDIDATE_MIN}


def reject(only: list[str], note: str = "人工看过，不是推广图") -> int:
    """把候选标为「不是推广图」，以后不再报，也绝不会被 promote-all 误删。"""
    data = _load()
    n = 0
    for fid, r in data.items():
        if fid in only and not r.get("confirmed"):
            r["rejected"] = True
            r["note"] = note
            n += 1
    _save(data)
    return n


def promote_all(only: list[str] | None = None) -> int:
    data = _load()
    n = 0
    for fid, r in data.items():
        if r.get("confirmed"):
            continue
        if only and fid not in only:
            continue
        if not only and r.get("tail", 0) < CANDIDATE_MIN:
            continue
        r["confirmed"] = True
        r["note"] = (r.get("note") or "") + "（人工确认）"
        n += 1
    _save(data)
    return n


def report_lines() -> list[str]:
    data = _load()
    conf = [(f, r) for f, r in data.items() if r.get("confirmed")]
    cand = candidates()
    lines = ["# 推广图黑名单", "",
             "由 build_epub.py 自动记账（每篇图片都登记），跨文章重复出现在文末的会升为候选。",
             "确认后执行 `./epub.sh ads --promote-all` 永久删除。", "",
             "## 已生效（%d）" % len(conf), ""]
    for fid, r in sorted(conf, key=lambda x: -x[1].get("count", 0)):
        lines.append("- `%s…`  %s  ×%d  %s"
                     % (fid[:12], r.get("note", ""), r.get("count", 0),
                        "、".join(r.get("books", []))))
    lines += ["", "## 待确认（%d）" % len(cand), ""]
    if not cand:
        lines.append("（暂无。积累了新的重复文末图会自动出现在这里。）")
    rejected = [(f, r) for f, r in data.items() if r.get("rejected")]
    if rejected:
        lines += ["", "## 已排除（%d，人工看过不是推广图，永不删除）" % len(rejected), ""]
        for fid, r in sorted(rejected, key=lambda x: -x[1].get("count", 0)):
            lines.append("- `%s…`  %s  ×%d  %s"
                         % (fid[:12], r.get("note", ""), r.get("count", 0),
                            "、".join(r.get("books", []))))
    for fid, r in sorted(cand.items(), key=lambda x: -x[1].get("count", 0)):
        lines.append("- `%s…`  ×%d（文末 %d 次）%s"
                     % (fid[:12], r.get("count", 0), r.get("tail", 0),
                        "、".join(r.get("books", []))))
        lines.append("  %s" % r.get("url", "")[:150])
    lines.append("")
    return lines


def main() -> int:
    import sys
    args = sys.argv[1:]
    if "--promote-all" in args:
        only = [a for a in args if not a.startswith("-")]
        n = promote_all(only or None)
        print("已确认 %d 个推广图，之后转换时会自动删除。" % n)
        return 0
    if "--save-report" in args:
        path = ENGINE_DIR.parent / "_推广图黑名单.md"
        path.write_text("\n".join(report_lines()), encoding="utf-8")
        print("已写入", path)
        return 0
    print("\n".join(report_lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
