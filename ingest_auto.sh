#!/usr/bin/env bash
# 定时扫描投递箱：每 4 小时跑一次 ingest（陈少 2026-10-13 定）。
#
#   ./ingest_auto.sh install    装成定时任务（每 4 小时）
#   ./ingest_auto.sh uninstall  取消
#   ./ingest_auto.sh status     看状态 + 上次跑了什么
#   ./ingest_auto.sh run        立刻手动跑一次
#
# 装好之后不依赖 WorkBuddy 是否开着，纯脚本，零消耗。日志：
#   ~/Library/Logs/epub-ingest.log
#
# 与 inbox_auto.sh 的分工：
#   inbox_auto  每小时扫「源料文件夹」（微信公众号下载），只处理微信
#   ingest_auto 每 4 小时扫「投递箱」，处理 Notion 导出（HTML/ZIP）与微信直取
set -uo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ENGINE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
LABEL="com.jessper.epub-ingest"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/epub-ingest.log"
UID_NUM="$(id -u)"
INTERVAL="${INGEST_INTERVAL:-14400}"    # 秒；默认 4 小时（陈少指定，别改小）
WATCH="/Users/jessper/Life/学习工厂/02、文稿成品线/01、投递箱"

cmd_install() {
  mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$ENGINE/ingest.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ENGINE</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><false/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLISTEOF
  local errfile="/tmp/epub-ingest-launch.err"
  : > "$errfile"
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>>"$errfile" || \
    launchctl load -w "$PLIST" 2>>"$errfile" || true
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "定时任务已装好：每 $((INTERVAL / 3600)) 小时扫一次 $WATCH"
    echo "日志：$LOG"
    echo "立刻跑一次：./epub.sh ingest-auto run"
    return 0
  fi
  echo "注册失败，launchd 返回："
  sed 's/^/    /' "$errfile" | tail -4
  echo "    （受限/沙箱环境里 launchctl 会被拒，请在本机终端里跑这条命令）"
  return 1
}

cmd_uninstall() {
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "定时任务已取消（要立刻扫还是可以 ./epub.sh ingest-auto run）"
}

cmd_status() {
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "定时任务：已装（每 $((INTERVAL / 3600)) 小时一次）"
  else
    echo "定时任务：未装"
  fi
  echo "盯的文件夹：$WATCH"
  if [ -d "$WATCH" ]; then
    local n
    n=$("$PY" -c 'import sys,pathlib;r=pathlib.Path(sys.argv[1]);print(sum(1 for p in r.iterdir() if p.is_file() and p.suffix.lower() in (".html",".htm",".zip")))' "$WATCH" 2>/dev/null)
    echo "  里面现有 ${n:-?} 个待处理文件（html / zip）"
  else
    echo "  （目录不存在）"
  fi
  local unk="$ENGINE/../02、定稿库/_待判"
  if [ -d "$unk" ]; then
    local m
    m=$("$PY" -c 'import sys,pathlib;r=pathlib.Path(sys.argv[1]);print(sum(1 for p in r.iterdir() if p.is_file()))' "$unk" 2>/dev/null)
    [ "${m:-0}" -gt 0 ] && echo "  ⚠ 待判目录里有 ${m} 个文件等你手工处理：$unk"
  fi
  if [ -f "$LOG" ]; then
    echo "上次日志尾部："
    tail -6 "$LOG" | sed 's/^/    /'
  else
    echo "还没跑过（没有日志）"
  fi
}

cmd_run() {
  echo "手动跑一次 ingest…"
  "$ENGINE/epub.sh" ingest
}

case "${1:-status}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  run) cmd_run ;;
  *) echo "用法：$0 {install|uninstall|status|run}"; exit 1 ;;
esac
