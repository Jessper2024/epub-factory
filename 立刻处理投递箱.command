#!/bin/zsh
# 立刻处理投递箱（不等 4 小时的定时）
# 双击即可。陈少 2026-10-13。
cd "/Users/jessper/Life/学习工厂/02、文稿成品线/_engine" || exit 1

echo "════════════════════════════════════════════════════"
echo "  立刻处理投递箱　$(date '+%Y-%m-%d %H:%M')"
echo "════════════════════════════════════════════════════"
echo

BOX="../01、投递箱"
N=$(ls "$BOX" 2>/dev/null | grep -v '^\.' | wc -l | tr -d ' ')
if [ "$N" = "0" ]; then
  echo "投递箱是空的，没有要处理的。"
  echo
  echo "按回车关闭…"; read; exit 0
fi

echo "投递箱里有 $N 个文件，开始处理…"
echo "────────────────────────────────────────────"
./epub.sh ingest
echo "────────────────────────────────────────────"
echo
./epub.sh ingest-auto status
echo
echo "按回车关闭…"
read
