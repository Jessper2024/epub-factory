#!/usr/bin/env python3
"""全量文章台账：一篇文章一条，说清它在哪本书、哪个月、源料还在不在。

    ./epub.sh ledger              终端看
    ./epub.sh ledger --md         另存一份 markdown 到项目根 _文章台账.md

两条来源拼起来：
  已进书   各号 xhtml/*.xhtml（文件名自带 日期_号_作者_标题）
  未收     源料文件夹里**判重指纹没命中**的 html（命中说明已经进过书了）

为什么台账里"已归档"的源文件显示成"已从源料清理"：
  清理时它们被移进了废纸篓，但每个在各号 原始HTML/ 里都留着副本，
  所以文章没丢，只是源料文件夹不再堆着重复的原件。
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))

from core import config as C                       # noqa: E402
import core.adapters            # noqa: F401,E402  触发注册，否则 pick 找不到任何 adapter
from core.pipeline import pick as pick_adapter     # noqa: E402
import build_epub as B                             # noqa: E402

XHTML_RE = re.compile(r"^(\d{4}年\d{1,2}月\d{1,2}日)_(.+?)(?:_([^_]+))?-Sigil\.xhtml$")

# 公众号 biz（唯一 ID）。老快照常常没有 js_name，但 biz 一定在——
# 它是比号名更可靠的归属依据：号名能改，biz 不能。
BIZ_RE = re.compile(r'biz:\s*""\s*\|\|\s*"([A-Za-z0-9=+/]+)"')
BIZ_URL_RE = re.compile(r"__biz=([A-Za-z0-9=+/]+)")


def biz_of(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            text = fh.read(300_000).decode("utf-8", "ignore")
    except Exception:
        return ""
    m = BIZ_RE.search(text) or BIZ_URL_RE.search(text)
    return m.group(1) if m else ""


def biz_owner_map() -> dict:
    """{biz: 号目录名} —— 从已归档的原文里学出来，不用人工维护。"""
    out: dict[str, str] = {}
    for book in B.collect_book_dirs():
        raw = book / "原始HTML"
        if not raw.is_dir():
            continue
        for f in raw.rglob("*.htm*"):
            b = biz_of(f)
            if b:
                out.setdefault(b, book.name)
    return out


def _month_of(date_cn: str) -> str:
    m = re.match(r"(\d{4})年(\d{1,2})月", date_cn or "")
    return "%s年%s月" % (m.group(1), int(m.group(2))) if m else "—"


def _ym(date_cn: str) -> str:
    m = re.match(r"(\d{4})年(\d{1,2})月", date_cn or "")
    return "%s%02d" % (m.group(1), int(m.group(2))) if m else ""


def archived_rows() -> list[dict]:
    """已进书的文章（源 = 各号 xhtml）。"""
    rows: list[dict] = []
    for book in B.collect_book_dirs():
        xdir = book / "xhtml"
        if not xdir.is_dir():
            continue
        epubs = sorted(book.glob("*.epub"))
        book_name = epubs[0].stem if epubs else "%s（未成书）" % book.name
        for p in sorted(xdir.glob("*.xhtml")):
            # 文件名格式固定为 日期_号_作者_标题-Sigil.xhtml；标题本身可能带下划线，
            # 所以按段切、不要把多余的段并进号名（会变成「猫笔刀_moomoocat」）。
            stem = p.name[:-len("-Sigil.xhtml")] if p.name.endswith("-Sigil.xhtml") else p.stem
            seg = stem.split("_")
            if re.match(r"^\d{4}年\d{1,2}月\d{1,2}日$", seg[0]):
                date = seg[0]
                account = seg[1] if len(seg) > 1 else book.name
                title = "_".join(seg[3:]) if len(seg) > 3 else (
                    seg[2] if len(seg) > 2 else account)
            else:
                date, account, title = "", book.name, stem
            raw = book / "原始HTML"
            rows.append({
                "date": date, "account": account or book.name, "title": title,
                "book": book_name, "month": _month_of(date), "state": "已进书",
                "has_raw": bool(list(raw.glob("*"))) if raw.is_dir() else False,
                "file": str(p.relative_to(ROOT)),
            })
    rows.sort(key=lambda r: (_ym(r["date"]), r["account"]), reverse=True)
    return rows


def pending_rows() -> list[dict]:
    """源料文件夹里还没进书的（指纹没命中 = 内容没进过任何一本书）。"""
    src = Path(C.get("watch_dir")).expanduser()
    if not src.is_dir():
        return []
    idx = B.fingerprint_index(dry_run=True)
    owners = biz_owner_map()
    rows: list[dict] = []
    for p in sorted(src.iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".html", ".htm"):
            continue
        if any(k in idx for k in B.content_fingerprints(p)):
            continue                      # 已归档，不在"未收"里
        doc = None
        try:
            ad = pick_adapter(p)
            if ad:
                doc = ad.extract(p)
        except Exception:
            doc = None
        rows.append({
            "date": (doc.date if doc else "") or "—",
            "account": (doc.account if doc else "") or "未识别",
            "title": (doc.title if doc else "") or p.stem,
            "book": "—", "month": _month_of(doc.date if doc else ""),
            "state": "未收", "has_raw": True,
            "file": p.name,
            "size_mb": p.stat().st_size / 1048576,
            # 号名抽不出来时靠 biz 兜底：biz 跟某个已归档号一致，就能确定归属
            "biz_owner": owners.get(biz_of(p), "") if (not doc or not doc.account) else "",
        })
    rows.sort(key=lambda r: (_ym(r["date"]), r["account"]), reverse=True)
    return rows


def _norm(title: str) -> str:
    """标题归一：去掉所有非字母数字中日韩字符，用来查"同一篇的另一个快照"。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", title or "").lower()


def mark_risks(archived: list[dict], pending: list[dict]) -> dict:
    """给未收的标两类风险。

    指纹判不出的同篇：同一篇文章存了两次、且第二次没有 og:url / var ct
    （老快照常见），两个键都算不出来，指纹查重就漏了——只能靠标题兜底。
    这时候如果直接收，同一篇会进两遍书。
    """
    known = {_norm(r["title"]): r for r in archived}
    accounts = {r["account"] for r in archived}
    dup = new = by_biz = 0
    for r in pending:
        hit = known.get(_norm(r["title"]))
        if hit:
            r["dupe_of"] = "%s / %s" % (hit["title"], hit["book"])
            r["risk"] = "疑似重复"
            dup += 1
        elif r.get("biz_owner"):
            # 号名抽不出，但 biz 跟某个已归档号一致 → 归属确定，可安全收
            r["risk"] = "biz 可归号"
            r["account"] = "%s（biz 判定）" % r["biz_owner"]
            by_biz += 1
        elif r["account"] == "未识别" or r["account"] not in accounts:
            r["risk"] = "新号未放行"
            new += 1
        else:
            r["risk"] = ""
    return {"dupe": dup, "new": new, "by_biz": by_biz}


def collect() -> dict:
    a, p = archived_rows(), pending_rows()
    books = sorted({r["book"] for r in a})
    pend_mb = sum(r.get("size_mb", 0) for r in p)
    risks = mark_risks(a, p)
    return {
        "archived": a, "pending": p, "books": books, "risks": risks,
        "stats": {"archived": len(a), "pending": len(p), "books": len(books),
                  "pending_mb": pend_mb, "generated": dt.datetime.now(), **risks},
    }


def to_md(data: dict) -> str:
    s = data["stats"]
    out = ["# 文章台账（全量）",
           "",
           "生成时间：%s" % s["generated"].strftime("%Y-%m-%d %H:%M"),
           "",
           "已进书 **%d** 篇 · 未收 **%d** 篇（%.0fMB） · %d 本书"
           % (s["archived"], s["pending"], s["pending_mb"], s["books"]),
           "", "## 已进书", "",
           "| 日期 | 公众号 | 标题 | 所在书 | 月份 | 原始HTML |",
           "|---|---|---|---|---|---|"]
    for r in data["archived"]:
        out.append("| %s | %s | %s | %s | %s | %s |"
                   % (r["date"], r["account"], r["title"].replace("|", "｜"),
                      r["book"], r["month"], "有" if r["has_raw"] else "无"))
    out += ["", "## 未收（源料文件夹，内容尚未进任何一本书）", "",
            "疑似重复 %d 篇 · biz 可归号 %d 篇 · 新号未放行 %d 篇"
            % (s["dupe"], s["by_biz"], s["new"]), "",
            "| 日期 | 公众号 | 标题 | 文件 | 体积 | 提示 |",
            "|---|---|---|---|---|---|"]
    for r in data["pending"]:
        note = ("⚠ 与已进书《%s》标题相同" % r["dupe_of"]) if r.get("dupe_of") else (
            "biz 判定属 %s，可安全收" % r["biz_owner"] if r["risk"] == "biz 可归号" else
            "新号，需 ./epub.sh allow 号名" if r["risk"] == "新号未放行" else "")
        out.append("| %s | %s | %s | %s | %.1fMB | %s |"
                   % (r["date"], r["account"], r["title"].replace("|", "｜"),
                      r["file"], r.get("size_mb", 0), note))
    return "\n".join(out) + "\n"


def main() -> int:
    data = collect()
    md = to_md(data)
    if "--md" in sys.argv:
        out = ROOT / "_文章台账.md"
        out.write_text(md, encoding="utf-8")
        print("台账已存：%s" % out)
    else:
        print(md[:4000])
        if len(md) > 4000:
            print("…（共 %d 行，加 --md 存全量）" % md.count("\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
