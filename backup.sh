#!/usr/bin/env bash
# 打包不可再生的源：各号的 xhtml/ 与 原始HTML/ 以及封面和代码。
# 成品 epub 可以随时从源重建，所以不进备份包。
# 用法：./backup.sh [目标目录]   默认 ~/Life/EPUB备份
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$HOME/Life/EPUB备份}"
mkdir -p "$DEST"
NAME="EPUB源_$(date +%Y%m%d-%H%M).tar.gz"

tar -czf "$DEST/$NAME" \
  -C "$ROOT" \
  --exclude='_engine/.venv' \
  --exclude='_engine/.git' \
  --exclude='*.epub' \
  --exclude='.DS_Store' \
  .

echo "备份完成：$DEST/$NAME"
echo "大小：$(du -h "$DEST/$NAME" | cut -f1)"
