#!/bin/zsh
# 处理投递箱（带实时进度页）—— 统一入口：html 入料线 + epub 拆解线
# 双击即可。陈少 2026-10-13 起，2026-10-14 加 epub 拆解线。
#
# 分流逻辑：
#   · .epub  → mv 到 0000、原始文件/，交给 auto_pipeline.sh（拆解→推Notion→落xhtml）
#   · .html/.zip → ingest.py（微信/Notion 入料 + 自动重建 epub）
# 两者各自独立处理，互不干扰。epub 拆解完打印「制卡」提示。
cd "/Users/jessper/Life/学习工厂/02、文稿成品线/_engine" || exit 1
PY="./.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

PORT=8770
URL="http://127.0.0.1:$PORT/"
PIDFILE="/tmp/epub-progress.pid"
BOX="../01、投递箱"
RAW="../04、EPUB拆解/0000、原始文件"
AUTO_PIPELINE="$HOME/.workbuddy/skills/anki-batch-cards/scripts/auto_pipeline.sh"

echo "════════════════════════════════════════════════════"
echo "  处理投递箱　$(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════════════"
echo

N=$(ls "$BOX" 2>/dev/null | grep -v '^\.' | wc -l | tr -d ' ')
if [ "$N" = "0" ]; then
  echo "投递箱是空的，没有要处理的。"
  echo
  echo "按回车关闭…"; read; exit 0
fi

echo "发现 $N 个待处理文件。"
echo

# ① 分流 epub：文件名繁转简（opencc）→ mv 到原始文件目录（同名跳过不覆盖）
mkdir -p "$RAW"
T2S="/Users/jessper/.workbuddy/binaries/python/envs/default/bin/python"
EPUB_N=0
for f in "$BOX"/*.epub; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  # 繁转简（opencc t2s），拿简体文件名；失败则保持原名
  sbase=$("$T2S" -c "import opencc,sys;print(opencc.OpenCC('t2s').convert(sys.argv[1]))" "$base" 2>/dev/null || echo "$base")
  if [ "$sbase" != "$base" ] && [ -n "$sbase" ]; then
    mv "$f" "$BOX/$sbase"
    f="$BOX/$sbase"
    echo "  🔤 繁转简：$base → $sbase"
  fi
  if [ -e "$RAW/$(basename "$f")" ]; then
    echo "  ⏭ epub 已存在源料目录，跳过：$(basename "$f")"
  else
    mv "$f" "$RAW/"
    EPUB_N=$((EPUB_N + 1))
    echo "  📦 epub → 原始文件：$(basename "$f")"
  fi
done

# ② 进度服务：已经在跑就复用，没跑才起（防重复双击起一堆）
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "进度服务已在运行，直接复用。"
else
  "$PY" progress_server.py --port "$PORT" > /tmp/epub-progress.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1.2
  echo "进度服务已启动。"
fi
echo "  进度页：$URL"
echo

# ③ 开浏览器
open "$URL" 2>/dev/null || echo "  （没自动打开，请手动访问上面的地址）"
sleep 0.4

# ④ 跑 ingest（html 入料线，前台；终端与进度页同时能看到进展）
echo "────────────────────────────────────────────────────"
echo "【入料线：html / zip】"
echo "────────────────────────────────────────────────────"
"$PY" ingest.py
echo

# ⑤ 跑 epub 拆解线（拆解 → 推 Notion → 落 xhtml）
if [ "$EPUB_N" -gt 0 ]; then
  echo "────────────────────────────────────────────────────"
  echo "【拆解线：epub】"
  echo "────────────────────────────────────────────────────"
  "$AUTO_PIPELINE"
  echo
  echo "✓ 拆解完成。"
  echo
  echo "──────────── 制卡提示 ────────────"
  echo "  拆解产物已落 04、EPUB拆解/<书名>/01、xhtml/"
  echo "  需要制卡时，回 WorkBuddy 跟助手说「<书名> 制卡」即可。"
  echo "────────────────────────────────────"
  echo
fi

echo "✓ 全部处理完成。"
echo
echo "进度页正在展示结果：$URL"
echo
echo "按回车关闭本终端窗口…"
read

echo "（进度服务仍在后台运行，方便你随时刷新页面。）
（不想要了就在终端跑：kill \$(cat $PIDFILE)）"
