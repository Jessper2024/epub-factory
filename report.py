#!/usr/bin/env python3
"""生成成书报表页面：~/Life/EPUB制作/_报表.html

自包含单文件（样式、脚本、封面缩略图全部内联），双击就能看，不需要联网。
数据全部来自实际文件扫描——HTML 源、XHTML、EPUB、推广图名单、回归基线，
所以它反映的就是磁盘上的真实状态，不是手填的。

    ./epub.sh report
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_epub as B  # noqa: E402
import promo as P  # noqa: E402
import regress as R  # noqa: E402
from core import config as C  # noqa: E402
from core import health as H  # noqa: E402   # 系统自检（依赖/磁盘/备份/结构…）

ROOT = B.ROOT
OUT = ROOT / "_报表.html"


def cover_thumb(book: Path, width: int = 200) -> str:
    """封面缩略图，转成 data URI 内联（报表不依赖外部文件）。"""
    cover = book / B.COVER_NAME
    if not cover.is_file():
        return ""
    try:
        from PIL import Image
        im = Image.open(cover).convert("RGB")
        h = int(im.height * width / im.width)
        im = im.resize((width, h))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=78)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def scan_account(book: Path) -> dict:
    """一个号的完整画像。"""
    src = B.source_dir(book)
    arts = []
    for p in sorted(src.glob("*.xhtml")):
        a = B.parse_article(p)
        if not a:
            continue
        text = "".join("".join(n.itertext()) for n in a.nodes)
        arts.append({
            "file": p.name,
            "date": "%04d-%02d-%02d" % a.date if a.date else "",
            "month": a.month_key,
            "title": a.title,
            "author": a.author or "",
            "chars": len(text),
            "images": sum(len(n.xpath('.//*[local-name()="img"]')) for n in a.nodes),
            # 小标题要数正文里的 h3：Article.subsections 只在打包过程中才被填充，
            # parse_article 之后永远是空的（数它就永远是 0）。
            "sections": sum(1 for n in a.nodes for sub in n.iter()
                            if isinstance(sub.tag, str)
                            and sub.tag.split("}")[-1] == "h3"),
        })
    arts.sort(key=lambda x: (x["date"], x["title"]))
    raw_dir = book / B.RAW_SUBDIR
    raw_files = sorted(raw_dir.glob("*.htm*")) if raw_dir.is_dir() else []
    epubs = []
    for ep in sorted(book.glob("*.epub")):
        st = B.epub_stats(ep)
        epubs.append({
            "name": ep.name,
            "size_mb": st["size_mb"],
            "articles": st["articles"],
            "months": st["months"],
            "toc": st["toc"],
            "mtime": dt.datetime.fromtimestamp(ep.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    months = sorted({a["month"] for a in arts if a["month"]})
    return {
        "account": book.name,
        "dir": str(book),
        "cover": cover_thumb(book),
        "xhtml_count": len(arts),
        "raw_count": len(raw_files),
        "raw_mb": round(sum(f.stat().st_size for f in raw_files) / 1048576, 1),
        "epubs": epubs,
        "span": ("%s ~ %s" % (months[0].replace("-", "年") + "月",
                              months[-1].replace("-", "年") + "月")) if months else "—",
        "articles": arts,
        "chars": sum(a["chars"] for a in arts),
        "images": sum(a["images"] for a in arts),
        "sections": sum(a["sections"] for a in arts),
        "months": months,
    }


def svg_bars(pairs: list[tuple[str, int]], height: int = 90, color: str = "#3B6D11") -> str:
    """手写 SVG 柱状图（不引外部库，离线可用）。"""
    if not pairs:
        return '<p class="muted">暂无数据</p>'
    w, gap = 34, 10
    total_w = len(pairs) * (w + gap)
    max_v = max(v for _, v in pairs) or 1
    bars = []
    for i, (label, v) in enumerate(pairs):
        h = max(3, int(v / max_v * (height - 26)))
        x = i * (w + gap)
        y = height - 20 - h
        bars.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{color}"/>'
            f'<text x="{x + w/2}" y="{y - 5}" text-anchor="middle" font-size="10" fill="#5F5E5A">{v}</text>'
            f'<text x="{x + w/2}" y="{height - 6}" text-anchor="middle" font-size="10" fill="#888780">{html.escape(label)}</text>')
    return (f'<svg viewBox="0 0 {total_w} {height}" width="100%" style="max-width:{total_w}px" '
            f'role="img">{"".join(bars)}</svg>')


def recent_activity(limit: int = 8) -> list[tuple[str, str]]:
    """最近动态：git 提交记录（跑不通就返回空，不影响报表）。"""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "log", "--pretty=%ad|%s", "--date=format:%m-%d %H:%M", "-n", str(limit)],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True, text=True, timeout=10)
        rows = []
        for line in out.stdout.strip().splitlines():
            if "|" in line:
                t, s = line.split("|", 1)
                rows.append((t, s))
        return rows
    except Exception:
        return []


def build_html(accounts: list[dict], now: str, live: bool = False,
               refresh_sec: int = 60) -> str:
    total_arts = sum(a["xhtml_count"] for a in accounts)
    total_raw = sum(a["raw_count"] for a in accounts)
    total_epub = sum(len(a["epubs"]) for a in accounts)
    total_mb = round(sum(e["size_mb"] for a in accounts for e in a["epubs"]), 1)
    total_chars = sum(a["chars"] for a in accounts)
    total_imgs = sum(a["images"] for a in accounts)
    total_secs = sum(a["sections"] for a in accounts)

    # 月度趋势
    month_count: dict[str, int] = {}
    for a in accounts:
        for m in a["months"]:
            month_count[m] = month_count.get(m, 0) + sum(
                1 for x in a["articles"] if x["month"] == m)
    month_pairs = [(m[2:].replace("-", "/"), month_count[m]) for m in sorted(month_count)]

    # 推广图
    plist = P._load()
    confirmed = sorted([(k, v) for k, v in plist.items() if v.get("confirmed")],
                       key=lambda x: -x[1].get("count", 0))
    cands = P.candidates()
    rej = sorted([(k, v) for k, v in plist.items() if v.get("rejected")],
                 key=lambda x: -x[1].get("count", 0))

    # 只有旧 epub、没有 xhtml 源的号（健康状态里要点名）
    missing_src = [a["account"] for a in accounts if not a["xhtml_count"]]

    # 还没成过书、在等攒够篇数的号（成书门槛）
    waiting = [(a["account"], B.publish_blocked(Path(a["dir"])))
               for a in accounts if not a["epubs"]]
    waiting = [(n, w) for n, w in waiting if w]

    # 名单外的新号：吸进来了但没点名，文件搁在 _待确认新号/，不建书、不混进别人的书
    pend = []
    pend_dir = ROOT / "_待确认新号"
    if pend_dir.is_dir():
        for d in sorted(pend_dir.iterdir()):
            if d.is_dir():
                n = sum(1 for f in d.iterdir() if f.is_file())
                if n:
                    pend.append((d.name, n))

    # 系统自检（依赖 / 磁盘 / 目录 / git / 备份 / 结构 / 近期异常 / 源料文件夹）
    # 自检自己炸了不能连累报表——报表是给人看的，少一张卡比整页空白好
    try:
        h = H.check_all()
    except Exception as e:
        h = {"score": 0, "level": "alert", "level_text": "自检异常", "checks": [],
             "detail": "%s: %s" % (type(e).__name__, e)}
    dot_for = {"ok": "ok", "warn": "warn", "fail": "fail"}
    checks_html = "".join(
        f'<div class="chk"><span class="dot {dot_for.get(c["status"], "warn")}"></span>'
        f'<span class="lb">{html.escape(c["label"])}</span>'
        f'<span class="muted">{html.escape(c["detail"])}</span></div>'
        for c in h.get("checks", []))

    # 备份概览（陈少 2026-09-17 要求写进仪表盘：一眼看到备份在哪、有几份、多久没打）
    bdir = Path(C.get("backup_dir")).expanduser()
    bprefix = C.get("backup_prefix") or ""
    b_ok = bdir.is_dir()
    bpkgs: list = []
    if b_ok:
        bpkgs = sorted(bdir.glob(f"{bprefix}*.tar.gz"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    btotal_mb = sum(p.stat().st_size for p in bpkgs) / 1024 / 1024
    bage = ""
    if bpkgs:
        days = int((dt.datetime.now()
                    - dt.datetime.fromtimestamp(bpkgs[0].stat().st_mtime)).days)
        bage = "今天" if days == 0 else f"{days} 天前"
    bverified = sum(1 for p in bpkgs if (Path(str(p) + ".sha256")).exists())
    bold = sorted((bdir / "旧版epub").glob("*")) if (bdir / "旧版epub").is_dir() else []
    brows = "".join(
        f'<div class="promo"><span class="mono">{html.escape(p.name)}</span>'
        f'<span class="pill grey">{p.stat().st_size / 1024 / 1024:.0f}MB</span>'
        f'<span class="pill grey">{html.escape(dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M"))}</span>'
        f'<span class="pill{" " if (Path(str(p) + ".sha256")).exists() else " warn"}">'
        f'{"有校验清单" if (Path(str(p) + ".sha256")).exists() else "缺清单"}</span></div>'
        for p in bpkgs[:6])

    # 回归
    base_file = R.BASE_FILE
    regress_state, regress_detail = "未建立基线", "先跑 ./epub.sh test --save"
    if base_file.exists():
        base = json.loads(base_file.read_text(encoding="utf-8"))
        cur = R.snapshot()
        diffs = R.compare(base, cur)
        if diffs:
            regress_state = "有差异 %d 处" % len(diffs)
            regress_detail = "；".join(diffs[:3])
        else:
            regress_state = "与基线一致"
            regress_detail = "%d 本已核对" % len(cur)

    # 处理报告
    rp = ROOT / "_处理报告.md"
    report_text = rp.read_text(encoding="utf-8") if rp.exists() else "（还没跑过）"

    # 最近动态（代码改动记录）
    acts = recent_activity()
    activity_html = ("".join(
        f'<div class="promo"><span class="mono">{html.escape(t)}</span>'
        f'<span>{html.escape(s)}</span></div>' for t, s in acts)
        or '<div class="muted">（读不到 git 记录）</div>')

    cards = [
        ("文章总数", total_arts, "篇（= 已归档 XHTML）"),
        ("HTML 源", total_raw, "个（原始 HTML 备份）"),
        ("EPUB 成品", total_epub, "本"),
        ("成品体积", "%.1f" % total_mb, "MB"),
        ("正文字数", "%.1f" % (total_chars / 10000), "万字"),
        ("配图", total_imgs, "张（全部内联）"),
        ("小标题", total_secs, "个（三级目录）"),
        ("公众号", len(accounts), "个"),
    ]
    card_html = "".join(
        f'<div class="card stat"><div class="k">{html.escape(k)}</div>'
        f'<div class="v">{v}</div><div class="s">{html.escape(s)}</div></div>'
        for k, v, s in cards)

    # 各号卡片
    acc_html = []
    for a in accounts:
        epubs = "".join(
            f'<div class="epub"><span class="mono">{html.escape(e["name"])}</span>'
            f'<span class="pill">{e["articles"]} 篇 · {e["months"]} 月 · 目录 {e["toc"]}</span>'
            f'<span class="pill grey">{e["size_mb"]}MB · {e["mtime"]}</span></div>'
            for e in a["epubs"])
        if not epubs:
            # 分清「没攒够篇数」和「压根没源」——前者是正常的，后者才要人管
            why = B.publish_blocked(Path(a["dir"]))
            epubs = (f'<div class="muted">暂未成书：{html.escape(why)}</div>' if why
                     else '<div class="muted">还没有成品（源文件缺失时无法重建）</div>')
        cov = (f'<img class="cover" src="{a["cover"]}" alt="{html.escape(a["account"])} 封面"/>'
               if a["cover"] else '<div class="cover none">无封面</div>')
        mini = svg_bars([(m[2:].replace("-", "/"),
                          sum(1 for x in a["articles"] if x["month"] == m))
                         for m in a["months"]], height=70)
        acc_html.append(f"""
      <section class="card acc">
        {cov}
        <div class="acc-main">
          <h3>{html.escape(a["account"])}</h3>
          <div class="row">
            <span class="pill">XHTML {a["xhtml_count"]} 篇</span>
            <span class="pill">HTML 源 {a["raw_count"]} 个（{a["raw_mb"]}MB）</span>
            <span class="pill">跨度 {html.escape(a["span"])}</span>
            <span class="pill">{a["images"]} 图</span>
            <span class="pill">{a["sections"]} 小标题</span>
            <span class="pill">{(a["chars"] / 10000):.1f} 万字</span>
          </div>
          <div class="epubs">{epubs}</div>
          <div class="mini">{mini}</div>
        </div>
      </section>""")

    # 文章总表
    rows = []
    for a in accounts:
        for x in a["articles"]:
            rows.append((x["date"], x["month"], a["account"], x["title"], x["author"],
                         x["chars"], x["images"], x["sections"]))
    rows.sort(key=lambda r: (r[0] or "", r[2]))
    tr = "".join(
        f'<tr data-s="{html.escape((r[2]+r[3]+r[4]).lower())}">'
        f'<td class="mono">{html.escape(r[0])}</td>'
        f'<td>{html.escape(r[2])}</td>'
        f'<td><b>{html.escape(r[3])}</b></td>'
        f'<td class="muted">{html.escape(r[4])}</td>'
        f'<td class="num">{r[5]:,}</td><td class="num">{r[6]}</td><td class="num">{r[7]}</td></tr>'
        for r in rows)

    def promo_row(items):
        if not items:
            return '<p class="muted">暂无</p>'
        return "".join(
            f'<div class="promo"><code>{html.escape(k[:16])}…</code>'
            f'<span class="pill grey">×{v.get("count", 0)}{" · 文末 " + str(v.get("tail", 0)) if v.get("tail") else ""}</span>'
            f'<span class="pill grey">{html.escape("、".join(v.get("books", [])))}</span>'
            f'<span class="muted">{html.escape(v.get("note", ""))}</span></div>'
            for k, v in items)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
{('<meta http-equiv="refresh" content="%d"/>' % refresh_sec) if (live and refresh_sec > 0) else ''}
<title>成书看板 · {now}</title>
<style>
  :root {{
    --bg: #F7F6F3; --card: #FFFFFF; --line: #E4E2DC; --text: #2C2C2A;
    --muted: #888780; --accent: #185FA5; --ok: #3B6D11; --warn: #854F0B;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.6 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }}
  header {{ background: #1F2A37; color: #fff; padding: 22px 28px; }}
  header h1 {{ margin: 0; font-size: 19px; font-weight: 500; }}
  header .meta {{ margin-top: 6px; font-size: 12px; color: #B4B2A9; }}
  header .meta code {{ background: rgba(255,255,255,.12); padding: 2px 6px; border-radius: 4px; }}
  header .refresh {{ color: #9FE1CB; text-decoration: none; border-bottom: 1px dashed #5DCAA5; }}
  main {{ max-width: 1120px; margin: 0 auto; padding: 22px 20px 60px; }}
  h2 {{ font-size: 15px; font-weight: 500; margin: 30px 0 12px; }}
  .grid {{ display: grid; gap: 12px; }}
  .stats {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }}
  .stat .k {{ font-size: 12px; color: var(--muted); }}
  .stat .v {{ font-size: 26px; font-weight: 500; margin: 2px 0; }}
  .stat .s {{ font-size: 11px; color: var(--muted); }}
  .health {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
  .health .card h4 {{ margin: 0 0 6px; font-size: 13px; font-weight: 500; }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }}
  .ok {{ background: var(--ok); }} .warn {{ background: var(--warn); }}
  .fail {{ background: #A32D2D; }}
  .chk {{ display: flex; gap: 8px; align-items: baseline; padding: 4px 0;
    border-bottom: 1px dashed var(--line); font-size: 12px; }}
  .chk:last-child {{ border-bottom: 0; }}
  .chk .lb {{ min-width: 62px; color: #5F5E5A; }}
  .acc {{ display: grid; grid-template-columns: 116px 1fr; gap: 16px; margin-bottom: 12px; }}
  .cover {{ width: 116px; border-radius: 6px; display: block; }}
  .cover.none {{ height: 155px; display: flex; align-items: center; justify-content: center;
    background: #F1EFE8; color: var(--muted); font-size: 12px; }}
  .acc h3 {{ margin: 0 0 8px; font-size: 16px; font-weight: 500; }}
  .row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
  .pill {{ font-size: 11px; background: #EAF3DE; color: var(--ok); border-radius: 20px; padding: 3px 9px; }}
  .pill.grey {{ background: #F1EFE8; color: #5F5E5A; }}
  .pill.warn {{ background: #FAEEDA; color: #854F0B; }}
  .epub {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 6px; }}
  .mono {{ font-family: ui-monospace, Menlo, monospace; font-size: 12px; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card);
    border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); font-size: 13px; }}
  th {{ background: #F1EFE8; font-weight: 500; font-size: 12px; color: #5F5E5A; position: sticky; top: 0; }}
  tr:hover td {{ background: #FBFAF7; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; color: #5F5E5A; }}
  .muted {{ color: var(--muted); font-size: 12px; }}
  #q {{ width: 100%; max-width: 320px; padding: 8px 12px; margin-bottom: 10px;
    border: 1px solid var(--line); border-radius: 8px; font-size: 13px; background: #fff; }}
  .promo {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 6px 0;
    border-bottom: 1px dashed var(--line); }}
  pre {{ background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 14px;
    overflow: auto; font-size: 12px; line-height: 1.7; }}
  .two {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
</style>
</head>
<body>
<header>
  <h1>成书{('看板' if live else '报表')}</h1>
  <div class="meta">
    {('每 %d 秒自动刷新 · 本页生成于 %s' % (refresh_sec, now)) if (live and refresh_sec > 0) else (('按需刷新 · 本页生成于 %s' % now) if live else ('生成于 %s　·　刷新：<code>cd ~/Life/EPUB制作/_engine &amp;&amp; ./epub.sh report</code>' % now))}
    　·　数据来自磁盘实际扫描，不自动重扫
    {('　·　<a class="refresh" href="/refresh">立即刷新</a>') if live else ''}
  </div>
</header>
<main>

  <h2>总览</h2>
  <div class="grid stats">{card_html}</div>

  <h2>健康状态</h2>
  <div class="health">
    <div class="card">
      <h4><span class="dot {'ok' if regress_state.startswith('与基线') else 'warn'}"></span>回归基线</h4>
      <div>{html.escape(regress_state)}</div>
      <div class="muted">{html.escape(regress_detail)}</div>
    </div>
    <div class="card">
      <h4><span class="dot {'ok' if not cands else 'warn'}"></span>推广图黑名单</h4>
      <div>已生效 {len(confirmed)} 条　·　待确认 {len(cands)}　·　已排除 {len(rej)}</div>
      <div class="muted">跨文章重复出现在文末的图会自动进候选，看图确认后再删</div>
    </div>
    <div class="card">
      <h4><span class="dot {'warn' if missing_src else 'ok'}"></span>缺源的书</h4>
      <div>{len(missing_src)} 个号没有 XHTML 源</div>
      <div class="muted">{('、'.join(missing_src) + ' 只有旧 epub、没有源，无法跟着规则升级（跑拆解流程补齐）') if missing_src else '每个号都有 XHTML 源，随时可以从源重建'}</div>
    </div>
    <div class="card">
      <h4><span class="dot {'warn' if waiting else 'ok'}"></span>成书门槛（{B.MIN_PUBLISH} 篇）</h4>
      <div>{len(waiting)} 个号还没成书，在等攒篇数</div>
      <div class="muted">{('；'.join('%s（%s）' % (n, w) for n, w in waiting) + '　说一声就能提前出书') if waiting else '已有成品的号不受门槛约束，照常跟着新增更新'}</div>
    </div>
    <div class="card">
      <h4><span class="dot {'warn' if pend else 'ok'}"></span>待确认新号</h4>
      <div>{len(pend)} 个号在等点名（共 {sum(n for _, n in pend)} 篇）</div>
      <div class="muted">{('；'.join('%s %d 篇' % (n, c) for n, c in pend) + '　—— 要收就 <code>./epub.sh allow 号名</code>，不要就不管，绝不会混进别的书') if pend else '盯的文件夹里没有未确认的新号'}</div>
    </div>
    <div class="card">
      <h4><span class="dot {'ok' if h['level'] == 'healthy' else ('fail' if h['level'] == 'alert' else 'warn')}"></span>系统自检</h4>
      <div>{h['score']} 分 · {html.escape(h['level_text'])}　<span class="muted">{html.escape(h.get('checked_at', ''))}</span></div>
      <div style="margin-top:6px">{checks_html or '<span class="muted">自检不可用</span>'}</div>
    </div>
  </div>

  <h2>备份 · {html.escape(bdir.name) if b_ok else '目标不可用'}</h2>
  <div class="card">
    {('<div class="pill warn">备份目标不可用（外接盘没插？）：' + html.escape(str(bdir)) + '</div>') if not b_ok else ''}
    <div class="row">
      <span class="pill">{len(bpkgs)} 份</span>
      <span class="pill grey">共 {btotal_mb:.0f} MB</span>
      <span class="pill">{('最新 ' + bage) if bage else '尚无备份'}</span>
      <span class="pill{' grey' if bverified < len(bpkgs) else ''}">{bverified}/{len(bpkgs)} 有校验清单</span>
      <span class="pill grey">旧版 epub {len(bold)} 份</span>
    </div>
    <div class="muted" style="margin:6px 0">{html.escape(str(bdir))}</div>
    {brows if brows else '<p class="muted">还没有备份 · 跑 <code>./epub.sh backup</code></p>'}
  </div>

  <h2>每月成文量</h2>
  <div class="card">{svg_bars(month_pairs, height=110, color="#185FA5")}</div>

  <h2>各公众号</h2>
  {''.join(acc_html)}

  <h2>全部文章（{len(rows)} 篇）</h2>
  <input id="q" placeholder="搜索标题 / 号名 / 作者…" oninput="filterRows()"/>
  <table>
    <thead><tr><th>日期</th><th>公众号</th><th>标题</th><th>作者</th><th class="num">字数</th>
      <th class="num">图</th><th class="num">小标题</th></tr></thead>
    <tbody id="tb">{tr}</tbody>
  </table>

  <h2>推广图名单</h2>
  <div class="two">
    <div class="card"><h4 style="margin:0 0 8px;font-size:13px">已生效（{len(confirmed)}）</h4>{promo_row(confirmed)}</div>
    <div class="card"><h4 style="margin:0 0 8px;font-size:13px">待确认（{len(cands)}）</h4>{promo_row(sorted(cands.items(), key=lambda x: -x[1].get('count', 0)))}</div>
  </div>
  <div class="card" style="margin-top:12px"><h4 style="margin:0 0 8px;font-size:13px">已排除（{len(rej)}，人工看过不是推广图，永不删除）</h4>{promo_row(rej)}</div>

  <h2>最近动态</h2>
  <div class="card">{activity_html}</div>

  <h2>最近一次处理报告</h2>
  <pre>{html.escape(report_text)}</pre>

  <h2>常用命令</h2>
  <pre>cd ~/Life/EPUB制作/_engine
./epub.sh            # 全部重建
./epub.sh inbox      # 收件箱 → 归档 → 成书（也自动吸入浏览器下载目录）
./epub.sh check      # 体检
./epub.sh test       # 回归比对
./epub.sh ads        # 推广图名单
./epub.sh report     # 重新生成本页</pre>
</main>
<script>
function filterRows() {{
  const q = document.getElementById('q').value.trim().toLowerCase();
  document.querySelectorAll('#tb tr').forEach(tr => {{
    tr.style.display = !q || tr.dataset.s.includes(q) ? '' : 'none';
  }});
}}
</script>
</body>
</html>
"""


def main() -> int:
    accounts = [scan_account(b) for b in B.collect_book_dirs()]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    OUT.write_text(build_html(accounts, now), encoding="utf-8")
    print("报表已生成：%s（%.0fKB，%d 个号，%d 篇文章）"
          % (OUT, OUT.stat().st_size / 1024, len(accounts),
             sum(a["xhtml_count"] for a in accounts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
