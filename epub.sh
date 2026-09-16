#!/usr/bin/env bash
# EPUB 工厂唯一入口。用法：
#   ./epub.sh             全部公众号重建
#   ./epub.sh only 猫刀笔   只重建某一本
#   ./epub.sh inbox       扫描收件箱 _待处理/ 并分流重建
#   ./epub.sh add a.html  归档指定文件并重建对应 EPUB
#   ./epub.sh list        只看扫描结果，不打包
#   ./epub.sh check       交付前体检（源 xhtml + EPUB 双向校验）
#   ./epub.sh toc 文件.epub  打印三级目录树
set -euo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ENGINE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

cmd="${1:-all}"
shift || true

case "$cmd" in
  all)   exec "$PY" "$ENGINE/build_epub.py" "$@" ;;
  only)  [ $# -ge 1 ] || usage; exec "$PY" "$ENGINE/build_epub.py" --only "$@" ;;
  inbox) if [ $# -ge 1 ]; then exec "$PY" "$ENGINE/build_epub.py" --inbox "$@"
         else exec "$PY" "$ENGINE/build_epub.py" --inbox; fi ;;
  add)   [ $# -ge 1 ] || usage; exec "$PY" "$ENGINE/build_epub.py" --add "$@" ;;
  list)  exec "$PY" "$ENGINE/build_epub.py" --list ;;
  check) exec "$PY" "$ENGINE/check_epub.py" "$@" ;;
  toc)   [ $# -ge 1 ] || usage; exec "$PY" "$ENGINE/dump_toc.py" "$@" ;;
  *)     usage ;;
esac
