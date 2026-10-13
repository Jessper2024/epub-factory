#!/bin/zsh
# 投递箱处理状态 · 一句话看懂
# 双击即可。陈少 2026-10-13。
cd "/Users/jessper/Life/学习工厂/02、文稿成品线/_engine" || exit 1

echo "════════════════════════════════════════════════════"
echo "  投递箱处理状态　$(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════════════"
echo

# ① 定时任务装没装 + 投递箱还剩几个
if launchctl print "gui/$(id -u)/com.jessper.epub-ingest" >/dev/null 2>&1; then
  echo "【定时任务】✓ 已装（每 4 小时自动扫一次）"
else
  echo "【定时任务】✗ 未装 —— 双击桌面「装投递箱定时任务.command」即可"
fi
echo

# ② 最近几次执行（这是"有没有在跑"最直接的答案）
echo "【最近 6 次执行】"
LOG="../_投递箱处理日志.md"
if [ -f "$LOG" ]; then
  # 表头两行 + 最后 6 行数据（表头用 head，数据从第 3 行起 tail）
  grep '^|' "$LOG" | head -2 | sed 's/^/  /'
  grep '^|' "$LOG" | tail -n +3 | tail -6 | sed 's/^/  /'
else
  echo "  （还没有执行记录）"
fi
echo

# ③ 投递箱里还剩什么
echo "【投递箱现状】"
BOX="../01、投递箱"
N=$(ls "$BOX" 2>/dev/null | grep -v '^\.' | wc -l | tr -d ' ')
if [ "$N" = "0" ]; then
  echo "  ✓ 空的（都处理完了）"
else
  echo "  还有 $N 个待处理："
  ls "$BOX" 2>/dev/null | grep -v '^\.' | sed 's/^/    /'
  echo
  echo "  → 想立刻处理：双击桌面「处理投递箱.command」"
fi
echo

# ④ 待判目录（需要人工处理的）
UNK="../02、定稿库/_待判"
if [ -d "$UNK" ]; then
  M=$(ls "$UNK" 2>/dev/null | grep -v '^\.' | wc -l | tr -d ' ')
  if [ "$M" != "0" ]; then
    echo "【⚠ 需要你手工处理】待判目录里有 $M 个文件（认不出平台/作者）"
    ls "$UNK" 2>/dev/null | sed 's/^/    /'
    echo "  位置：~/Life/学习工厂/02、文稿成品线/02、定稿库/_待判/"
    echo
  fi
fi

echo "────────────────────────────────────────────"
echo "  日志全文：~/Life/学习工厂/02、文稿成品线/_投递箱处理日志.md"
echo "  系统日志：~/Library/Logs/epub-ingest.log"
echo
echo "按回车关闭…"
read
