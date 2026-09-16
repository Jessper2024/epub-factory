#!/usr/bin/env bash
# 备份：3-2-1 策略
#   3 份副本（1 本地 + N 异地）· 2 种介质（异地目标配到移动硬盘即满足）· 1 份离线
#
# 只打不可再生的源：各号 xhtml/ 与 原始HTML/、封面、代码。
# 成品 epub 能从源重建，不进包（省一半体积）。
#
# 核心资产 = 各号 xhtml/（单篇文稿本体，最值钱、丢了不可再生）。
# 陈少 2026-09-17 明确要求：xhtml 源必须有**看得见、拿得到**的备份，
# 不能只是"打进 tar 里说包含"。所以每份备份做两件事：
#   ① tar 包里含 xhtml，并写 .manifest.json 记录篇数，打包前后强校验（数量对不上直接报错）
#   ② 额外以原文件形式镜像到 <备份目录>/源镜像/<号>/xhtml/，拔盘插别的机器可直接打开单篇，不用解包
#
# 用法
#   ./backup.sh                 打一份到本地默认目录（~/Life/EPUB备份）
#   ./backup.sh <目标目录>       打到指定目录（移动硬盘直接指过去）
#   ./backup.sh list            列现有备份（含年龄、大小、校验状态）
#   ./backup.sh verify <包>      校验某一个包（SHA256 + tar 可读性 + 文件数）
#   ./backup.sh verify-all      校验本地所有包
#   ./backup.sh clean           按保留策略清理旧包（默认留 5 份）
#
# 每份备份旁会有两个附属文件：
#   <包>.sha256          校验清单（防"备份了但是坏的"）
#   <包>.manifest.json   核心资产清单：xhtml 篇数 / 原始HTML 数 / 各号明细 / 代码版本
# 另外 <备份目录>/源镜像/<号>/xhtml/ 是核心资产的原文件副本，可直接打开单篇。
#
# 异地目标写在 config.local.json 的 backup_targets（数组），打了本地会自动复制过去：
#   .venv/bin/python -c "from core import config; config.save_local(backup_targets=['/Volumes/移动硬盘/EPUB备份'])"
set -euo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$ENGINE/.." && pwd)"
PY="$ENGINE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
# 备份位置与命名从 config 读（与 core/health 同一真相源），下面 cfg() 定义后再赋值

# 算单个文件的 SHA256。macOS 原生是 shasum；Linux 是 sha256sum。
# 注意：macOS 的 /sbin/sha256sum 虽然存在，但**不支持 -c 校验选项**，
# 所以下面一律手工比对，不用 `sha256sum -c`（踩过：校验永远失败，误报"包已损坏"）。
if command -v shasum >/dev/null 2>&1; then
  sha_of() { shasum -a 256 "$1" | awk '{print $1}'; }
elif command -v sha256sum >/dev/null 2>&1; then
  sha_of() { sha256sum "$1" | awk '{print $1}'; }
else
  sha_of() { echo ""; }
fi

# 手工比对：清单文件内容是 "hash  name"
# 用 awk 取字段，不用 `read` —— read 在 set -u 下对空变量敏感，容易悄悄失败
sha_check() {
  local sumfile="$1"
  local dir; dir="$(cd "$(dirname "$sumfile")" && pwd)"
  local expect; expect="$(awk 'NR==1{print $1}' "$sumfile")"
  local name; name="$(awk 'NR==1{print $2}' "$sumfile")"
  [ -n "$name" ] && [ -f "$dir/$name" ] || return 1
  local actual; actual="$(sha_of "$dir/$name")"
  [ -n "$expect" ] && [ "$expect" = "$actual" ]
}

cfg() { "$PY" -c "
from core import config
import json,sys
v = config.get('$1', $2)
print(json.dumps(v, ensure_ascii=False) if isinstance(v,(list,dict)) else v)
" 2>/dev/null || echo "$2"; }

# 与 core/health 共用同一份定义，避免"打了备份却查不到"
# 注意 cfg 的第二个参数会直接嵌进 python 代码，必须是**带引号的字面量**：
# 传 "$HOME/..." 展开成 /Users/... 会让 python 报语法错、cfg 静默 fallback 到默认值
# （踩过：明明改了 backup_dir 指向外接盘，备份却还打在本地）。
DEFAULT_DEST="$(cfg backup_dir "'$HOME/Life/EPUB备份'")"
PREFIX="$(cfg backup_prefix 'EPUB源_')"

# ── 核心资产统计 ──────────────────────────────────────────────
# 只数真文件，跳过 macOS 的 ._* AppleDouble（tar 默认会存这些元数据副本，
# 不跳过会让计数翻倍，误以为"篇数对不上"）。
src_stats() {
  "$PY" - "$ROOT" <<'PYEOF'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
xhtml, raw, per = 0, 0, {}
for d in sorted(root.iterdir()):
    if not d.is_dir() or d.name.startswith(("_", ".")):
        continue
    xs = sorted((d / "xhtml").glob("*.xhtml")) if (d / "xhtml").is_dir() else []
    rd = d / "原始HTML"
    rs = [p for p in rd.rglob("*") if p.is_file() and not p.name.startswith("._")] if rd.is_dir() else []
    if xs:
        per[d.name] = {"xhtml": len(xs), "raw": len(rs)}
    xhtml += len(xs)
    raw += len(rs)
print(json.dumps({"xhtml": xhtml, "raw": raw, "per": per}, ensure_ascii=False))
PYEOF
}

pkg_stats() {
  "$PY" - "$1" <<'PYEOF'
import json, tarfile, sys
t = tarfile.open(sys.argv[1], "r:gz")
xhtml = raw = 0
for m in t.getmembers():
    if not m.isfile() or m.name.rsplit("/", 1)[-1].startswith("._"):
        continue
    parts = m.name.split("/")
    if len(parts) >= 3 and parts[-2] == "xhtml" and m.name.endswith(".xhtml"):
        xhtml += 1
    elif "原始HTML" in m.name:
        raw += 1
print(json.dumps({"xhtml": xhtml, "raw": raw}))
PYEOF
}

# 以原文件形式镜像核心资产（xhtml + 原始HTML）到备份目录。
# 增量覆盖、**不做 --delete**：备份的语义是累积，源目录万一误删，镜像里还得留着。
mirror_sources() {
  local dest="$1" mirror="$dest/源镜像"
  mkdir -p "$mirror" || { echo "  ! 源镜像目录建不了：$mirror"; return 1; }
  local d name n=0
  for d in "$ROOT"/*/; do
    [ -d "$d/xhtml" ] || continue
    name="$(basename "$d")"
    mkdir -p "$mirror/$name"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a "$d/xhtml/" "$mirror/$name/xhtml/"
      [ -d "$d/原始HTML" ] && rsync -a "$d/原始HTML/" "$mirror/$name/原始HTML/"
    else
      cp -R "$d/xhtml" "$mirror/$name/" 2>/dev/null
      [ -d "$d/原始HTML" ] && cp -R "$d/原始HTML" "$mirror/$name/" 2>/dev/null
    fi
    n=$((n + 1))
  done
  echo "  → 源镜像（原文件，可直接打开）：$mirror（$n 个号）"
}

cmd_backup() {
  local dest="${1:-$DEFAULT_DEST}"
  local gitrev
  gitrev="$(git -C "$ENGINE" rev-parse --short HEAD 2>/dev/null || echo '?')"

  # 外接盘没插时 mkdir 会失败——必须明确报错，绝不能静默"备份成功"
  # （静默失败是备份系统最坏的故障：你以为有备份，其实一份都没有）
  if ! mkdir -p "$dest" 2>/dev/null; then
    echo "✗ 备份目标不可用：$dest"
    echo "  外接盘没插、或路径不存在。插上后重跑；此期间系统处于「无异地副本」状态，"
    echo "  ./epub.sh health 会持续提醒。"
    return 1
  fi
  if ! touch "$dest/.write-probe" 2>/dev/null; then
    echo "✗ 备份目标不可写：$dest（磁盘写保护或已满？）"
    return 1
  fi
  rm -f "$dest/.write-probe"

  local name="${PREFIX}$(date +%Y%m%d-%H%M).tar.gz"
  local path="$dest/$name"

  # 打包前先数一遍核心资产，打完再数一遍包里的——两遍对不上就说明漏了
  local before
  before="$(src_stats)"
  echo "核心资产：$("$PY" -c "
import json,sys
d=json.loads(sys.argv[1])
print('xhtml %d 篇 · 原始HTML %d 个' % (d['xhtml'], d['raw']))" "$before")"

  echo "打包中…（含 xhtml 源与原始HTML，成品 epub 不进包）"
  # COPYFILE_DISABLE=1：不让 macOS 往 tar 里塞 ._* 元数据副本（条目翻倍、计数失真）
  COPYFILE_DISABLE=1 tar -czf "$path" \
    -C "$ROOT" \
    --exclude='_engine/.venv' \
    --exclude='_engine/.git' \
    --exclude='*.epub' \
    --exclude='.DS_Store' \
    --exclude='_审计' \
    .

  # 校验清单：备份没校验等于没备份
  local sum
  sum="$(sha_of "$path")"
  printf '%s  %s\n' "$sum" "$name" > "$path.sha256"

  echo "备份完成：$path"
  echo "  大小 $(du -h "$path" | cut -f1)　SHA256 ${sum:0:16}…"

  # 包内复核：核心资产到底进没进包，用数字说话，不靠"应该包含了"
  local after verdict
  after="$(pkg_stats "$path")"
  verdict="$("$PY" - "$path" "$before" "$after" "$gitrev" <<'PYEOF'
import datetime, json, pathlib, sys
path, before, after, git = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3]), sys.argv[4]
ok = after["xhtml"] == before["xhtml"] and before["xhtml"] > 0
manifest = {
    "pkg": pathlib.Path(path).name,
    "created": datetime.datetime.now().isoformat(timespec="seconds"),
    "git": git,
    "source": before,
    "in_pkg": after,
    "mirror": "源镜像",
    "verified": ok,
}
pathlib.Path(path + ".manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if ok:
    print("OK  包内含 xhtml %d 篇 / 原始HTML %d 个（与源一致）" % (after["xhtml"], after["raw"]))
else:
    print("BAD 包内 xhtml %d 篇 ≠ 源 %d 篇" % (after["xhtml"], before["xhtml"]))
PYEOF
)"
  case "$verdict" in
    OK*)  echo "  ✓ ${verdict#OK  }" ;;
    BAD*) echo "  ✗ ${verdict#BAD }——这份备份不完整，先别用它恢复（包已保留，便于排查）" ;;
  esac

  # 旧版 epub 备份目录（拆书前的旧成品）。主包里排除了 *.epub（成品能从源重建），
  # 但这一份是历史遗留、没有对应的源，所以单独以原文件形式存一份，方便直接取用。
  local old
  for old in "$ROOT"/_旧版备份_*; do
    [ -d "$old" ] || continue
    mkdir -p "$dest/旧版epub"
    if cp -R "$old" "$dest/旧版epub/" 2>/dev/null; then
      echo "  → 旧版 epub 副本：$dest/旧版epub/$(basename "$old")"
    else
      echo "  ! 旧版 epub 复制失败：$old"
    fi
  done

  # 源镜像：核心资产以原文件形式再存一份，拔盘插别的机器能直接打开单篇，不用解 tar
  mirror_sources "$dest" || echo "  ! 源镜像未完成（tar 包仍然可用）"

  # 异地副本（3-2-1 的另外 2 份）
  local targets
  targets="$(cfg backup_targets '[]')"
  if [ "$targets" != "[]" ] && [ -n "$targets" ]; then
    echo "$targets" | "$PY" -c "
import json,sys,shutil,os
for t in json.load(sys.stdin):
    t = os.path.expanduser(t)
    try:
        os.makedirs(t, exist_ok=True)
        shutil.copy2('$path', t)
        shutil.copy2('$path.sha256', t)
        print('  → 异地副本', t)
    except Exception as e:
        print('  ! 异地失败', t, type(e).__name__, e)
"
  else
    echo "  未配异地目标（当前只有 1 份副本，不符合 3-2-1）"
    echo "  配置：.venv/bin/python -c \"from core import config; config.save_local(backup_targets=['/Volumes/你的盘/EPUB备份'])\""
  fi

  cmd_clean "$dest" >/dev/null 2>&1 || true
}

cmd_list() {
  local dest="${1:-$DEFAULT_DEST}"
  [ -d "$dest" ] || { echo "没有备份目录：$dest"; return 0; }
  echo "备份目录：$dest"
  local n=0
  for f in "$dest"/${PREFIX}*.tar.gz; do
    [ -e "$f" ] || continue
    n=$((n + 1))
    local age size ok="?"
    # 注意是命令替换 $() 不是算术展开 $(( ))——写错会把 python 源码当算术表达式求值并打印
    age="$("$PY" -c "
import os,time
print(int((time.time()-os.path.getmtime('$f'))//86400))
" 2>/dev/null || echo '?')"
    size="$(du -h "$f" | cut -f1)"
    if [ -f "$f.sha256" ]; then
      if sha_check "$f.sha256"; then ok="✓"; else ok="✗损坏"; fi
    else
      ok="无清单"
    fi
    # 核心资产篇数（清单里读得到就显示，旧包显示 -）
    local xh="-"
    if [ -f "$f.manifest.json" ]; then
      xh="$("$PY" -c "
import json,sys
print(json.load(open(sys.argv[1],encoding='utf-8')).get('source',{}).get('xhtml',0))" \
        "$f.manifest.json" 2>/dev/null || echo '?')"
    fi
    printf '  %s  %-34s %6s  %s 天前  xhtml %s 篇\n' "$ok" "$(basename "$f")" "$size" "$age" "$xh"
  done
  [ "$n" -eq 0 ] && echo "  （还没有备份）"
}

cmd_verify() {
  local pkg="$1"
  [ -f "$pkg" ] || { echo "文件不存在：$pkg"; return 1; }
  local ok=1
  if [ -f "$pkg.sha256" ]; then
    if sha_check "$pkg.sha256"; then
      echo "✓ SHA256 一致"
    else
      echo "✗ SHA256 不一致——包已损坏，不要用这份恢复"; ok=0
    fi
  else
    echo "! 没有校验清单，跳过哈希比对"
  fi
  # tar 可读性 + 文件数
  local cnt
  if cnt="$(tar -tzf "$pkg" 2>/dev/null | wc -l | tr -d ' ')"; then
    echo "✓ 可解压，含 $cnt 个条目"
  else
    echo "✗ 无法读取归档"; ok=0
  fi
  # 核心资产核对：有清单就按清单核篇数；没清单的旧包退回粗检
  local mf="$pkg.manifest.json"
  if [ -f "$mf" ]; then
    local cur
    cur="$(pkg_stats "$pkg" 2>/dev/null || echo '{}')"
    if ! "$PY" - "$mf" "$cur" <<'PYEOF'
import json, sys
mf = json.loads(open(sys.argv[1], encoding="utf-8").read())
try:
    cur = json.loads(sys.argv[2])
except Exception:
    cur = {}
exp = mf.get("source", {}).get("xhtml", 0)
got = cur.get("xhtml", 0)
if got == exp and exp > 0:
    print("✓ xhtml 源 %d 篇 · 原始HTML %d 个（与清单一致，清单存于 %s）"
          % (got, cur.get("raw", 0), mf.get("created", "?")))
else:
    print("✗ xhtml 源 %d 篇 ≠ 清单 %d 篇——这份备份不完整，别用它恢复" % (got, exp))
    sys.exit(1)
PYEOF
    then ok=0; fi
  else
    local has
    has="$(tar -tzf "$pkg" 2>/dev/null | grep -c '/xhtml/' || true)"
    if [ "${has:-0}" -gt 0 ]; then
      echo "✓ 含 xhtml 源（$has 个路径）— 旧包无清单，未核篇数"
    else
      echo "✗ 包里没有 xhtml 源——这份备份是废的"; ok=0
    fi
  fi
  [ "$ok" -eq 1 ] && echo "结论：这份备份可用" || echo "结论：不可用"
  return $((1 - ok))
}

cmd_verify_all() {
  local dest="${1:-$DEFAULT_DEST}"
  [ -d "$dest" ] || { echo "没有备份目录：$dest"; return 0; }
  local bad=0 n=0
  for f in "$dest"/${PREFIX}*.tar.gz; do
    [ -e "$f" ] || continue
    n=$((n + 1))
    echo "── $(basename "$f")"
    cmd_verify "$f" | sed 's/^/   /' || bad=$((bad + 1))
  done
  [ "$n" -eq 0 ] && echo "（还没有备份）" || echo "共 $n 份，异常 $bad 份"
}

cmd_clean() {
  local dest="${1:-$DEFAULT_DEST}"
  local keep
  keep="$(cfg backup_keep 5)"
  [ -d "$dest" ] || return 0
  # 只动自己命名的备份包，绝不碰目录里别的东西
  "$PY" - "$dest" "$keep" <<'PYEOF'
import sys, pathlib, re
dest, keep = pathlib.Path(sys.argv[1]), int(sys.argv[2])
pkgs = sorted(dest.glob("EPUB源_*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
for old in pkgs[keep:]:
    for suf in ("", ".sha256"):
        p = pathlib.Path(str(old) + suf)
        if p.exists():
            p.unlink()
            print("  删除旧备份", p.name)
print(f"  保留 {min(len(pkgs), keep)} 份（策略 keep={keep}）")
PYEOF
}

case "${1:-backup}" in
  backup)     cmd_backup "${2:-}" ;;
  list)       cmd_list "${2:-}" ;;
  verify)     [ $# -ge 2 ] || { echo "用法：$0 verify <包>"; exit 1; }; cmd_verify "$2" ;;
  verify-all) cmd_verify_all "${2:-}" ;;
  clean)      cmd_clean "${2:-}" ;;
  *) echo "用法：$0 {backup [目录]|list|verify <包>|verify-all|clean}"; exit 1 ;;
esac
