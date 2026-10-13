#!/usr/bin/env bash
# EPUB 工厂唯一入口。用法：
#   ./epub.sh             全部公众号重建
#   ./epub.sh only 猫笔刀   只重建某一本
#   ./epub.sh inbox       扫描收件箱 _待处理/ 并分流重建
#                         加 --dry-run 预演（只报告不落地）；--min-articles 10 改门槛
#   ./epub.sh ingest      统一入料：微信直取 / Notion 导出（HTML / ZIP）→ 判平台 → 判作者 → 归档
#   ./epub.sh ingest-auto 投递箱定时任务：install / status / run（每 4 小时扫一次）
#   ./epub.sh progress    投递箱实时进度页（只监听 127.0.0.1:8770，自动开浏览器）
#   ./epub.sh migrate-md  存量 md → xhtml（默认预演，--apply 执行）
#   ./epub.sh allow 号名    放行一个新号（把搁置在 _待确认新号/ 的文件放回收件箱）
#   ./epub.sh auto        定时任务：install / uninstall / status / run（默认每小时扫一次）
#   ./epub.sh add a.html  归档指定文件并重建对应 EPUB
#   ./epub.sh list        只看扫描结果，不打包
#   ./epub.sh check       交付前体检（源 xhtml + EPUB 双向校验）
#   ./epub.sh toc 文件.epub  打印三级目录树
#   ./epub.sh dash        常驻看板（本机 http://127.0.0.1:8760，随时看运行情况）
#   ./epub.sh report      生成报表页面 _报表.html
#   ./epub.sh ads         查看/维护推广图黑名单
#   ./epub.sh backfill    给已归档的 HTML 补记图片账
#   ./epub.sh sync        把代码改动提交并推送到 GitHub 仓库
#   ./epub.sh cloud       云端看板：publish（生成报表→git push）/ install（装定时任务）/ status
#   ./epub.sh health      系统自检（依赖/磁盘/git/备份/结构/近期异常），输出评分
#   ./epub.sh backup      备份不可再生资产；verify <包> 校验、list 列清单、clean 老化
#   ./epub.sh cleanup     空间清理（预演默认；--apply 走废纸篓删中间产物/旧成品/已归档源料）
#   ./epub.sh ledger      全量文章台账（已进书 + 未收，标疑似重复/可归号/新号）；--md 存成文件
#   ./epub.sh emoji       全量回改源 xhtml：给表情图补 class=emoji + 内联样式（--dry-run 预演）
set -euo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ENGINE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

usage() {
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

cmd="${1:-all}"
shift || true

case "$cmd" in
  all)   exec "$PY" "$ENGINE/build_epub.py" "$@" ;;
  only)  [ $# -ge 1 ] || usage; exec "$PY" "$ENGINE/build_epub.py" --only "$@" ;;
  inbox) exec "$PY" "$ENGINE/build_epub.py" --inbox "$@" ;;
  ingest) exec "$PY" "$ENGINE/ingest.py" "$@" ;;      # 统一入料：微信/Notion HTML/ZIP → 平台/作者
  ingest-auto) exec "$ENGINE/ingest_auto.sh" "$@" ;;  # 投递箱定时任务：install/status/run（每 4 小时）
  progress) exec "$PY" "$ENGINE/progress_server.py" "$@" ;;  # 实时进度页（只监听本机 8770）
  migrate-md) exec "$PY" "$ENGINE/migrate_md.py" "$@" ;;  # 存量 md → xhtml（预演；--apply 执行）
  allow) [ $# -ge 1 ] || usage; exec "$PY" "$ENGINE/build_epub.py" --allow "$@" ;;
  auto)  exec "$ENGINE/inbox_auto.sh" "$@" ;;
  add)   [ $# -ge 1 ] || usage; exec "$PY" "$ENGINE/build_epub.py" --add "$@" ;;
  list)  exec "$PY" "$ENGINE/build_epub.py" --list ;;
  test)  exec "$PY" "$ENGINE/regress.py" "$@" ;;
  split) exec "$PY" "$ENGINE/build_epub.py" --split-year "$@" ;;
  ads)   exec "$PY" "$ENGINE/promo.py" "$@" ;;
  backfill) exec "$PY" "$ENGINE/build_epub.py" --backfill-ads ;;
  report) exec "$PY" "$ENGINE/report.py" ;;
  dash)  exec "$ENGINE/dash_service.sh" "$@" ;;
  cloud) exec "$ENGINE/cloud_publish.sh" "$@" ;;
  health) cd "$ENGINE" || exit 1; exec "$PY" -m core.health "$@" ;;
  backup) exec "$ENGINE/backup.sh" "$@" ;;
  sync)  cd "$ENGINE" || exit 1
         git add -A
         # 本次没改动也要推：上次可能"提交成功、推送失败"（GitHub 偶发 500），
         # 留下 ahead 的本地提交。不推就会一直以为同步过了，其实远端是旧的。
         if ! git diff --cached --quiet; then
           git commit -q -m "自动同步 $(date +'%Y-%m-%d %H:%M')"
         fi
         if git push; then
           echo "已同步到 https://github.com/Jessper2024/epub-factory"
         else
           echo "✗ 推送失败（网络或 GitHub 故障）。本地提交已保留，稍后重跑 ./epub.sh sync"
           exit 1
         fi ;;
  check) exec "$PY" "$ENGINE/check_epub.py" "$@" ;;
  cleanup) exec "$PY" "$ENGINE/cleanup.py" "$@" ;;   # 预演；--apply 才真删（走废纸篓）
  ledger)  exec "$PY" "$ENGINE/ledger.py" "$@" ;;    # 全量文章台账；--md 存成 _文章台账.md
  emoji)  exec "$PY" "$ENGINE/tag_emoji_sources.py" "$@" ;;  # 全量回改源 xhtml：补 emoji 标记+样式
  toc)   [ $# -ge 1 ] || usage; exec "$PY" "$ENGINE/dump_toc.py" "$@" ;;
  *)     usage ;;
esac
