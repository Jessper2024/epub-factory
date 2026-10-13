#!/bin/zsh
# 处理投递箱（带实时进度页）
# 双击即可。陈少 2026-10-13。
#
# 设计要点（为什么是前台跑、服务后台留）：
#   · 进度服务后台跑，**跑完不关**——这样终端关了页面还能继续刷
#   · ingest 前台跑，终端能看到完整输出（出错时方便看）
#   · 同一时间只允许一个进度服务（防重复双击起一堆）
cd "/Users/jessper/Life/学习工厂/02、文稿成品线/_engine" || exit 1
PY="./.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

PORT=8770
URL="http://127.0.0.1:$PORT/"
PIDFILE="/tmp/epub-progress.pid"

echo "════════════════════════════════════════════════════"
echo "  处理投递箱　$(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════════════"
echo

BOX="../01、投递箱"
N=$(ls "$BOX" 2>/dev/null | grep -v '^\.' | wc -l | tr -d ' ')
if [ "$N" = "0" ]; then
  echo "投递箱是空的，没有要处理的。"
  echo
  echo "按回车关闭…"; read; exit 0
fi

echo "发现 $N 个待处理文件。"
echo

# ① 进度服务：已经在跑就复用，没跑才起（防重复双击起一堆）
RUNNING=0
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  RUNNING=1
  echo "进度服务已在运行，直接复用。"
else
  "$PY" progress_server.py --port "$PORT" > /tmp/epub-progress.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1.2
  echo "进度服务已启动。"
fi
echo "  进度页：$URL"
echo

# ② 开浏览器
open "$URL" 2>/dev/null || echo "  （没自动打开，请手动访问上面的地址）"
sleep 0.4

# ③ 跑 ingest（前台，终端与进度页同时能看到进展）
echo "────────────────────────────────────────────────────"
"$PY" ingest.py
echo "────────────────────────────────────────────────────"
echo

echo "✓ 处理完成。"
echo
echo "进度页正在展示结果：$URL"
echo "页面会一直开着（服务在后台），看完了直接关浏览器就行。"
echo
echo "按回车关闭本终端窗口…"
read

echo "（进度服务仍在后台运行，方便你随时刷新页面。）
（不想要了就在终端跑：kill \$(cat $PIDFILE)）"
