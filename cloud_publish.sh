#!/bin/zsh
# 云端看板发布：生成报表 → git commit → push 到 GitHub（Pages 自动刷新）。
#
#   ./cloud_publish.sh [--force]  发布（--force 无视改动也 commit）
#   ./cloud_publish.sh status     看上次发布状态
#   ./cloud_publish.sh install    装定时任务（每 4 小时）
#   ./cloud_publish.sh uninstall  取消定时任务
#
# 日志：~/Library/Logs/epub-cloud.log
set -uo pipefail

ENGINE="$(cd "$(dirname "$0")" && pwd)"
PY="$ENGINE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
LABEL="com.jessper.epub-cloud"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/epub-cloud.log"
# Pages 根路径只认 index.html；且下划线开头的文件会被 Jekyll 忽略，所以仓库内必须叫 index.html
REPORT="$ENGINE/index.html"          # git 仓库内的报表（Pages 从这里读）
LOCAL_REPORT="$ENGINE/../_报表.html"    # 本地报表（数据源，陈少本地看这个）
NOJEKYLL="$ENGINE/.nojekyll"           # 禁用 Jekyll，避免下划线开头的资源被吞
INTERVAL="${CLOUD_INTERVAL:-14400}"   # 秒；默认 4 小时

# ---------- 参数解析 ----------
FORCE=0
SUBCMD="publish"

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    status|publish|install|uninstall) SUBCMD="$1"; shift ;;
    *) echo "用法：$0 {publish|status|install|uninstall} [--force]"; exit 1 ;;
  esac
done

# ---------- 函数定义 ----------

cmd_publish() {
  mkdir -p "$LOG_DIR"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始云端发布…" >> "$LOG"

  # 1. 生成报表
  cd "$ENGINE" || exit 1
  "$PY" "$ENGINE/report.py" >> "$LOG" 2>&1 || {
    echo "  报表生成失败"; tail -3 "$LOG" | sed 's/^/    /'; return 1;
  }

  # 2. 报表复制进仓库根目录，命名 index.html（Pages 根路径默认文件）
  REPO_REPORT="$ENGINE/index.html"
  if [ -f "$LOCAL_REPORT" ]; then
    cp -f "$LOCAL_REPORT" "$REPO_REPORT"
  fi
  touch "$NOJEKYLL"   # 禁用 Jekyll

  # 3. 检查是否有改动（用 status --porcelain：git diff 对未跟踪的新文件永远"无差异"）
  cd "$ENGINE" || exit 1
  local dirty
  dirty="$(git status --porcelain -- "$REPO_REPORT" "$NOJEKYLL" 2>/dev/null)"
  if [ -z "$dirty" ] && [ $FORCE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 报表无改动，跳过发布" >> "$LOG"
    echo "报表无改动，跳过"
    return 0
  fi

  # 4. commit + push
  git add -A "$REPO_REPORT" "$NOJEKYLL" >> "$LOG" 2>&1 || true
  if git diff --cached --quiet 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 暂存区无改动" >> "$LOG"
    echo "暂存区无改动"
    return 0
  fi
  git commit -q -m "看板更新 $(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1 || {
    echo "  commit 失败"; tail -3 "$LOG" | sed 's/^/    /'; return 1;
  }
  git push >> "$LOG" 2>&1 || {
    echo "  push 失败"; tail -3 "$LOG" | sed 's/^/    /'; return 1;
  }
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 云端发布成功" >> "$LOG"
  echo "已发布：$(git log -1 --format='%h %s')"
}

cmd_status() {
  echo "云端发布："
  if [ -f "$LOG" ]; then
    echo "上次发布："
    tail -3 "$LOG" | sed 's/^/  /'
  else
    echo "  还没跑过"
  fi
  echo "盯的报表：$REPORT"
  if [ -f "$REPORT" ]; then
    echo "  报表大小：$(du -h "$REPORT" | cut -f1)"
    echo "  报表时间：$(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "$REPORT")"
  else
    echo "  （报表不存在，先跑一次 ./epub.sh report）"
  fi
}

cmd_install() {
  mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
  local errfile="/tmp/epub-cloud-launch.err"
  : > "$errfile"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ENGINE/cloud_publish.sh</string>
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
  local UID_NUM="$(id -u)"
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>>"$errfile" || \
    launchctl load -w "$PLIST" 2>>"$errfile" || true
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "云端定时任务已装好：每 $((INTERVAL / 60)) 分钟发布一次"
    echo "日志：$LOG"
    echo "立刻跑一次：./epub.sh cloud --force"
    return 0
  fi
  echo "注册失败，launchd 返回："
  sed 's/^/    /' "$errfile" | tail -4
  echo "    （受限/沙箱环境里 launchctl 会被拒，请在本机终端里跑这条命令）"
  return 1
}

cmd_uninstall() {
  local UID_NUM="$(id -u)"
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "云端定时任务已取消"
}

# ---------- 分发 ----------

case "$SUBCMD" in
  publish)   cmd_publish ;;
  status)    cmd_status ;;
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  *) echo "用法：$0 {publish|status|install|uninstall} [--force]"; exit 1 ;;
esac
