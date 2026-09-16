#!/usr/bin/env bash
# 成书看板的常驻服务管理。
#   ./dash_service.sh start      后台起服务并开浏览器（已在跑就只开浏览器）
#   ./dash_service.sh stop       停掉看板服务
#   ./dash_service.sh status     看当前状态
#   ./dash_service.sh install    装成登录自启（LaunchAgent，开机就能看）
#   ./dash_service.sh uninstall  取消登录自启
#
# 端口默认 8760，可用 DASH_PORT 覆盖。只监听 127.0.0.1，数据不出本机。
set -uo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ENGINE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
PORT="${DASH_PORT:-8760}"
URL="http://127.0.0.1:$PORT/"
LABEL="com.jessper.epub-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/epub-dashboard.log"
PIDFILE="/tmp/epub-dashboard.pid"
UID_NUM="$(id -u)"

# 本机请求绕开系统代理，否则会被代理拦成 502
probe() { curl -s --noproxy '*' -o /dev/null --max-time 1 "$URL"; }

# 端口上响应的是不是我们自己的看板（认 Server 头，不依赖 ps）
is_our_server() {
  curl -s --noproxy '*' -D - -o /dev/null --max-time 2 "$URL" 2>/dev/null | grep -qi '^Server: *EpubDash'
}

# 只听 LISTEN 的进程：浏览器连过 8760 会留下 CLOSE_WAIT 连接，不能算进来（否则会误杀浏览器）
port_pids() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true; }

# 优先用命令行核对（有 ps 时更准）；ps 不可用就靠上面的指纹兜底
our_pids() {
  local p cmd
  for p in $(port_pids); do
    cmd="$(ps -o command= -p "$p" 2>/dev/null || true)"
    if [ -n "$cmd" ]; then
      case "$cmd" in *dashboard.py*) echo "$p" ;; esac
    elif is_our_server; then
      echo "$p"
    fi
  done
}

cmd_start() {
  if probe; then
    echo "看板已在运行：$URL"
    open "$URL"
    return 0
  fi
  mkdir -p "$LOG_DIR"
  nohup "$PY" "$ENGINE/dashboard.py" --port "$PORT" --no-open >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  disown 2>/dev/null || true
  local i
  for i in $(seq 1 12); do
    probe && break
    sleep 0.4
  done
  if probe; then
    echo "看板已启动：$URL"
    echo "日志：$LOG"
    open "$URL"
  else
    echo "启动失败，看看 $LOG"
    return 1
  fi
}

cmd_stop() {
  local p n=0
  if [ -f "$PIDFILE" ]; then
    p="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "${p:-}" ] && kill -0 "$p" 2>/dev/null; then kill "$p" 2>/dev/null && n=$((n + 1)); fi
    rm -f "$PIDFILE"
  fi
  for p in $(our_pids); do
    if kill -0 "$p" 2>/dev/null; then kill "$p" 2>/dev/null && n=$((n + 1)); fi
  done
  sleep 0.5
  for p in $(our_pids); do kill -9 "$p" 2>/dev/null || true; done
  # 仍有响应且确认是我们自己的看板，才动端口的进程（别的程序占着就不碰）
  if probe && is_our_server; then
    for p in $(port_pids); do kill -9 "$p" 2>/dev/null || true; done
    sleep 0.5
    n=$((n + 1))
  fi
  if [ "$n" -gt 0 ]; then echo "已停止看板服务"; else echo "看板本来就没在运行"; fi
  if [ -f "$PLIST" ]; then
    echo "提示：登录自启还开着，下次登录会自动再起。要关掉跑 ./dash_service.sh uninstall"
  fi
}

cmd_status() {
  if probe; then
    echo "状态：运行中　$URL"
    local p
    for p in $(port_pids); do
      echo "进程：PID $p　$(ps -o lstart= -p "$p" 2>/dev/null | tr -s ' ' || echo '')"
    done
  else
    echo "状态：未运行（$URL 无响应）"
  fi
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "登录自启：已安装（开机自动起）"
  elif [ -f "$PLIST" ]; then
    echo "登录自启：未生效（plist 残留但 launchd 没加载，跑 ./dash_service.sh install 重试）"
  else
    echo "登录自启：未安装"
  fi
}

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
    <string>$ENGINE/dashboard.py</string>
    <string>--port</string><string>$PORT</string>
    <string>--no-open</string>
  </array>
  <key>WorkingDirectory</key><string>$ENGINE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLISTEOF
  local errfile="/tmp/epub-dashboard-launch.err"
  : > "$errfile"
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
  # 手工起的实例先让位，否则它占着端口、launchd 那份会漂到 8761
  local p
  for p in $(our_pids); do kill "$p" 2>/dev/null || true; done
  rm -f "$PIDFILE"
  sleep 0.5
  for p in $(our_pids); do kill -9 "$p" 2>/dev/null || true; done

  launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>>"$errfile" || \
    launchctl load -w "$PLIST" 2>>"$errfile" || true

  local i
  for i in $(seq 1 15); do
    probe && break
    sleep 0.4
  done
  if probe; then
    echo "登录自启已装好：$URL（以后开机自动起）"
    return 0
  fi
  # load -w 失败时也返回 0，所以不能信退出码，得直接问 launchd
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "已注册到 launchd，但服务还没起来。看日志：$LOG"
  else
    echo "注册失败，看板没起来。launchd 返回："
    sed 's/^/    /' "$errfile" | tail -4
    echo "    （沙箱/受限环境下 launchctl 会被拒，在普通终端或双击 .command 里跑通常没问题）"
  fi
  return 1
}

cmd_uninstall() {
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || launchctl unload -w "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "登录自启已取消（服务已停，要看的时候跑 ./看板.command 就行）"
}

case "${1:-start}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; sleep 0.5; cmd_start ;;
  status) cmd_status ;;
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  *) echo "用法：$0 {start|stop|restart|status|install|uninstall}"; exit 1 ;;
esac
