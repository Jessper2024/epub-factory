#!/usr/bin/env bash
# 备份：3-2-1 策略
#   3 份副本（1 本地 + N 异地）· 2 种介质（异地目标配到移动硬盘即满足）· 1 份离线
#
# 只打不可再生的源：各号 xhtml/ 与 原始HTML/、封面、代码。
# 成品 epub 能从源重建，不进包（省一半体积）。
#
# 用法
#   ./backup.sh                 打一份到本地默认目录（~/Life/EPUB备份）
#   ./backup.sh <目标目录>       打到指定目录（移动硬盘直接指过去）
#   ./backup.sh list            列现有备份（含年龄、大小、校验状态）
#   ./backup.sh verify <包>      校验某一个包（SHA256 + tar 可读性 + 文件数）
#   ./backup.sh verify-all      校验本地所有包
#   ./backup.sh clean           按保留策略清理旧包（默认留 5 份）
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

cmd_backup() {
  local dest="${1:-$DEFAULT_DEST}"

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

  echo "打包中…（只含不可再生的源，epub 不进包）"
  tar -czf "$path" \
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
    printf '  %s  %-34s %6s  %s 天前\n' "$ok" "$(basename "$f")" "$size" "$age"
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
  # 关键内容抽查：必须有至少一个号的 xhtml
  local has
  has="$(tar -tzf "$pkg" 2>/dev/null | grep -c '/xhtml/' || true)"
  if [ "${has:-0}" -gt 0 ]; then
    echo "✓ 含 xhtml 源（$has 个路径）"
  else
    echo "✗ 包里没有 xhtml 源——这份备份是废的"; ok=0
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
