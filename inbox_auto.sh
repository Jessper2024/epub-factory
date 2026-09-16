#!/usr/bin/env bash
# 定时扫描「盯的那个文件夹」：默认每小时一次，把新文章收进对应公众号的书里。
#
#   ./inbox_auto.sh install    装成定时任务（每小时跑一次 inbox）
#   ./inbox_auto.sh uninstall  取消定时任务
#   ./inbox_auto.sh status     看状态 + 上次跑了什么
#   ./inbox_auto.sh run        立刻手动跑一次（不管定时）
#
# 装好之后不依赖 WorkBuddy 是否开着，纯脚本，零消耗。日志：
#   ~/Library/Logs/epub-inbox.log
set -uo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ENGINE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
LABEL="com.jessper.epub-inbox"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/epub-inbox.log"
UID_NUM="$(id -u)"
INTERVAL="${INBOX_INTERVAL:-3600}"      # 秒；默认 1 小时
WATCH="$HOME/Downloads/微信公众号下载"

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
    <string>$ENGINE/build_epub.py</string>
    <string>--inbox</string>
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
  local errfile="/tmp/epub-inbox-launch.err"
  : > "$errfile"
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>>"$errfile" || \
    launchctl load -w "$PLIST" 2>>"$errfile" || true
  # load 失败时也返回 0，所以问 launchd 要答案
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "定时任务已装好：每 $((INTERVAL / 60)) 分钟扫一次 $WATCH"
    echo "日志：$LOG"
    echo "立刻跑一次：./epub.sh auto run"
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
  echo "定时任务已取消（要立刻扫还是可以 ./epub.sh auto run）"
}

cmd_status() {
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "定时任务：已装（每 $((INTERVAL / 60)) 分钟一次）"
  else
    echo "定时任务：未装"
  fi
  echo "盯的文件夹：$WATCH"
  if [ -d "$WATCH" ]; then
    local n
    # 用 Python 数，不用 find：某些受限环境里 find 会被截断，报出来的数不对（踩过）
    n=$("$PY" -c 'import sys,pathlib;r=pathlib.Path(sys.argv[1]);print(sum(1 for p in r.rglob("*") if p.is_file() and p.suffix.lower() in (".html",".htm")))' "$WATCH" 2>/dev/null)
    echo "  里面现有 ${n:-?} 个 html"
  else
    echo "  （目录不存在）"
  fi
  if [ -f "$LOG" ]; then
    echo "上次日志尾部："
    tail -6 "$LOG" | sed 's/^/    /'
  else
    echo "还没跑过（没有日志）"
  fi
}

cmd_run() {
  echo "手动跑一次 inbox…"
  "$ENGINE/epub.sh" inbox
  echo "完成。看板：http://127.0.0.1:8760/　处理报告：$ENGINE/../_处理报告.md"
}

case "${1:-status}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  run) cmd_run ;;
  *) echo "用法：$0 {install|uninstall|status|run}"; exit 1 ;;
esac
