#!/bin/zsh
# ═══ 学习工厂 · 唯一处理入口 ═══
# 双击一次：起进度页 → 四线自动处理 → 装定时任务 → 刷新看板 → 推 GitHub。
# 零交互（抓视频走对话）。
#
# 四线：
#   ① 文稿线  ingest.py            扫 02、文稿成品线/01、投递箱（html/zip/epub）
#   ② 成人线  intake_dispatch.py    扫 03、成人作品集线/{演员}/01、投递箱（CSV）
#   ③ 写真线  make_epub.py（遍历）   扫 04、写真集线/01、投递箱（图片文件夹）
#   ④ 定时任务 + 看门狗（首次装，已装跳过）
# 收尾：刷新全部看板 + 推 GitHub。

set -uo pipefail
PY="/Users/jessper/.workbuddy/binaries/python/envs/default/bin/python"
FACTORY="$HOME/Life/学习工厂"
ENGINE="$FACTORY/02、文稿成品线/_engine"

echo "════════════════════════════════════════════════════"
echo "  学习工厂 · 统一处理　$(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════════════"
echo

# ═══ ⓪ 进度页（后台起，跑完不关）═══
PORT=8770
PIDFILE="/tmp/epub-progress.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "  进度页已在运行：http://127.0.0.1:$PORT/"
else
  cd "$ENGINE"
  "$PY" progress_server.py --port "$PORT" > /tmp/epub-progress.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1.2
  echo "  进度页已启动：http://127.0.0.1:$PORT/"
fi
open "http://127.0.0.1:$PORT/" 2>/dev/null
echo

# ═══ ① 文稿线 ═══
echo "──────────────── ① 文稿线（html / zip / epub）────────────────"
cd "$ENGINE"
"$PY" ingest.py
echo

# ═══ ② 成人线 ═══
echo "──────────────── ② 成人线（CSV）────────────────"
ADULT="$HOME/.workbuddy/skills/edge-batch-capture/scripts/intake_dispatch.py"
if [ -f "$ADULT" ]; then
  "$PY" "$ADULT" 2>&1 | tail -20
else
  echo "  （成人线脚本不存在，跳过）"
fi
echo

# ═══ ③ 写真线 ═══
echo "──────────────── ③ 写真线（图片文件夹）────────────────"
PHOTO="$HOME/.workbuddy/skills/photobook-epub/scripts/make_epub.py"
PHOTO_BOX="$FACTORY/04、写真集线/01、投递箱"
PHOTO_OUT="$FACTORY/04、写真集线/02、成书"
if [ -d "$PHOTO_BOX" ]; then
  n=0
  for d in "$PHOTO_BOX"/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    out="$PHOTO_OUT/$name.epub"
    if [ -f "$out" ]; then
      continue
    fi
    "$PY" "$PHOTO" --input "$d" --output "$out" --title "$name" --sequencer "沪上陈少" >/dev/null 2>&1
    if [ -f "$out" ]; then
      echo "  ✓ 写真集：$name"
      n=$((n+1))
    else
      echo "  ✗ 写真集失败：$name"
    fi
  done
  echo "  写真线新出 $n 本。"
else
  echo "  （写真线投递箱不存在，跳过）"
fi
echo

# ═══ ④ 定时任务 + 看门狗（首次装）═══
echo "──────────────── ④ 定时任务 / 看门狗 ────────────────"
# 文稿线定时任务（每 4 小时自动扫投递箱）
if launchctl print "gui/$(id -u)/com.jessper.epub-ingest" >/dev/null 2>&1; then
  echo "  ✓ 文稿线定时任务已装，跳过。"
else
  cd "$ENGINE"
  ./epub.sh ingest-auto install >/dev/null 2>&1
  echo "  ✓ 已装文稿线定时任务（每 4 小时自动扫）。"
fi
# 成人线抓取看门狗（每 15 分钟巡检，卡住重启）
WD_PLIST="$HOME/Library/LaunchAgents/com.user.crawlwatch.plist"
if launchctl list 2>/dev/null | grep -q crawlwatch; then
  echo "  ✓ 抓取看门狗已在运行，跳过。"
elif [ -f "$WD_PLIST" ]; then
  launchctl bootstrap "gui/$(id -u)" "$WD_PLIST" 2>/dev/null || launchctl load "$WD_PLIST" 2>/dev/null
  echo "  ✓ 已加载抓取看门狗。"
fi
echo

# ═══ ⑤ 刷新看板 + 推 GitHub ═══
echo "──────────────── ⑤ 刷新看板 + 推 GitHub ────────────────"
"$PY" "$HOME/.workbuddy/skills/dashboard-hub/scripts/dashboard.py" 2>&1 | tail -3

BOARD_DIR="$FACTORY/00、看板中枢"
if [ -d "$BOARD_DIR/.git" ]; then
  cd "$BOARD_DIR"
  git add -A
  git -c user.name="Jessper2024" -c user.email="jessper@users.noreply.github.com" \
      commit -q -m "刷新看板 $(date '+%Y-%m-%d %H:%M:%S')" 2>/dev/null || true
  git -c http.proxy= -c https.proxy= -c http.version=HTTP/1.1 push origin main 2>&1 | tail -1
fi
echo

echo "════════════════════════════════════════════════════"
echo "  ✓ 全部处理完成。进度页：http://127.0.0.1:$PORT/"
echo "════════════════════════════════════════════════════"
echo
echo "按回车关闭本窗口…（进度页仍在后台，可随时刷新）"
read
