#!/bin/zsh
# ═══ 学习工厂 · 唯一处理入口 ═══
# 双击一次：起进度页 → 多线自动处理 → 装定时任务 → 刷新看板 → 推 GitHub。
# 零交互（抓视频走对话）。
#
# 处理顺序：
#   ⓪⁺ 视频文稿线     ingest_video_csv.py   扫 01、视频文稿线/01、投递箱 CSV → 抓字幕+弹幕+建集目录（B站/YouTube）
#   ⓪⁺² 视频文稿归位   route_pending.py      扫 01、视频文稿线/01、投递箱 手投字幕稿（合集名-序号-标题_文稿.txt）→ 归到对应集目录改名 _字幕_L1.txt
#   ① 读书笔记       reading_notes.py     扫 01、投递箱 KoodoReader/Koodo Reader html + *clipping*.txt（先搬走，避免被 ingest 标待判）
#   ①⁺ EPUB 拆解     epub-to-xhtml/split.sh  完整 .epub 先移入 04、EPUB拆解/0000、原始文件/ 再拆成编号 xhtml（不碰 Notion）
#   ② 文稿线         ingest.py            扫 02、文稿成品线/01、投递箱（微信三种形态/Notion/html/zip，含防 Koodo 抢跑兜底）
#   ②⁺ 作者目录归一  merge_accounts.py     六镇→九边 / 龙虎榜三板斧→猫笔刀 / tony→xtony
#   ③ 成人线         intake_dispatch.py    扫 03、成人作品集线/{演员}/01、投递箱（CSV）
#   ④ 写真线         make_epub.py（遍历）   扫 04、写真集线/01、投递箱（图片文件夹）
#   ⑤ 定时任务 + 看门狗（首次装，已装跳过）
# 收尾：刷新全部看板 + 推 GitHub。

set -uo pipefail
PY="/Users/jessper/.workbuddy/binaries/python/envs/default/bin/python"
FACTORY="$HOME/Life/学习工厂"
ENGINE="$FACTORY/02、文稿成品线/_engine"

# ═══ 运行日志（每次双击全程落盘，问题排查直接读这个文件，不用再问陈少）═══
# 日志路径：~/Life/学习工厂/02、文稿成品线/_engine/_command运行日志.log
# 每次运行：打分隔线+时间戳；stdout+stderr 全量追加；终端窗口同步显示（tee）。
RUN_LOG="$ENGINE/_command运行日志.log"
# 只保留最近 800 行，防止日志无限膨胀
if [ -f "$RUN_LOG" ]; then
  tail -n 800 "$RUN_LOG" > "$RUN_LOG.tmp" 2>/dev/null && mv "$RUN_LOG.tmp" "$RUN_LOG"
fi
echo ""
echo "════════════════════════════════════════════════════" >> "$RUN_LOG"
echo "  运行开始　$(date '+%Y-%m-%d %H:%M:%S')　PID=$$" >> "$RUN_LOG"
echo "════════════════════════════════════════════════════" >> "$RUN_LOG"
# stdout+stderr 同时写日志 + 显示到终端（tee -a 追加，终端仍实时可见）
exec > >(tee -a "$RUN_LOG") 2>&1

# 进度上报辅助：写进 _进度.json 的某个板块（跨段共用）
# 用法：report <板块key> <名称> <状态> <详情> [去向]
report() {
  "$PY" - "$@" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "/Users/jessper/Life/学习工厂/02、文稿成品线/_engine")
try:
    from progress import report as _r
    _r(sys.argv[1], sys.argv[2], sys.argv[3],
       sys.argv[4] if len(sys.argv) > 4 else "",
       sys.argv[5] if len(sys.argv) > 5 else "")
except Exception:
    pass
PY
}

# 开跑前清掉上次遗留的孤儿「处理中」（长任务被杀时来不及改终态，页面会永远转圈）
"$PY" -c "
import sys
sys.path.insert(0, '/Users/jessper/Life/学习工厂/02、文稿成品线/_engine')
try:
    from progress import clear_stale_running
    n = clear_stale_running()
    if n:
        print('  已清理上次遗留的「处理中」孤儿状态 %d 条 → 标记中断' % n)
except Exception:
    pass
"

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

# ═══ ⓪⁺ 视频文稿线（投递箱 CSV → 抓字幕+弹幕+建集目录）═══
echo "──────────────── ⓪⁺ 视频文稿线（CSV 抓取）────────────────"
VIDEO_INBOX="$FACTORY/01、视频文稿线/01、投递箱"
VIDEO_CSV_INGEST="$HOME/.workbuddy/skills/video-subtitle-fetcher/tools/ingest_video_csv.py"
if [ -f "$VIDEO_CSV_INGEST" ]; then
  # CSV 字段「序号/标题/链接/作者」，按链接域名分流 B站/YouTube，一个链接一个集目录；
  # 本地零 Token（调 run.py flow 抓字幕+弹幕），抓完 CSV 归档进 _已处理CSV/。
  # 抓取过程**实时透传输出**（每个视频逐行打出来），陈少能看到「第 N/M 个、抓取中、完成」。
  # 注意：抓取是长任务，CSV 已「先归档再抓取」（脚本内），中断也不会滞留投递箱。
  "$PY" "$VIDEO_CSV_INGEST" "$VIDEO_INBOX"
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "  ✓ 视频 CSV 全部抓取完成"
  else
    echo "  ⚠ 视频 CSV 抓取有失败（rc=$rc），看上面明细"
  fi
else
  echo "  （视频 CSV 抓取脚本不存在，跳过）"
fi
echo

# ═══ ⓪⁺² 视频文稿归位（陈少手投的字幕 L1 稿「合集名 - 序号 - 标题_文稿.txt」→ 自动归到对应集目录，改名 _字幕_L1.txt）═══
echo "──────────────── ⓪⁺² 视频文稿归位（手投字幕稿 → 集目录）────────────────"
ROUTE_PY="$HOME/.workbuddy/skills/video-subtitle-fetcher/tools/route_pending.py"
if [ -f "$ROUTE_PY" ]; then
  "$PY" "$ROUTE_PY" --go
else
  echo "  （route_pending.py 不存在，跳过）"
fi
echo

# ═══ ① 读书笔记（前置：先搬走 Koodo/Kindle，避免被文稿线 ingest 当成待判）═══
echo "──────────────── ① 读书笔记（Koodo Reader / Kindle）────────────────"
cd "$ENGINE"
# 统一入口：投递箱里 KoodoReader/Koodo Reader html 与 *clipping*.txt 自动分流处理，
# 合并进 09、读书笔记/{Koodo Reader 读书笔记, Kindle 读书笔记}/<书名>/<书名>.xhtml
# 规则：多文件合并去重（原文相同留第一次时间戳）、保留日期、不保留 Color、暂不进 EPUB。
# 不判断「空壳」：html 哪怕只有 1 个字也照常启动处理，是否空壳由陈少人工判断。
# 部分重复标注：精确去重后，一条包含另一条或相似度≥0.85 的笔记对标橙色 + 出 _部分重复_待核对.md 供陈少人工核对（Koodo/Kindle 共用）。
"$PY" reading_notes.py --inbox 2>&1 | tail -20
echo

# ═══ ①⁺ EPUB 拆解 + 推 Notion（完整 EPUB：拆成编号 xhtml 存 04、EPUB拆解/，再自动推「图书库拆解中转库」）═══
echo "──────────────── ①⁺ EPUB 拆解 + 推 Notion（完整 epub）────────────────"
cd "$ENGINE"
EPUB_SPLIT="$HOME/.workbuddy/skills/epub-to-xhtml/scripts/split.sh"
EPUB_PUSH="$HOME/.workbuddy/skills/epub-to-xhtml/scripts/notion/push.sh"
EPUB_SRC="$FACTORY/02、文稿成品线/04、EPUB拆解/0000、原始文件"
EPUB_PARENT="3e2641b1-9740-8010-97dd-e247925cb8c9"   # 图书库拆解中转库 database UUID
if [ -f "$EPUB_SPLIT" ]; then
  n=0
  # (N) = NULL_GLOB：无 .epub 时展开为空，不报 no matches found（否则 command 在这里崩掉，后面段全不跑）
  for f in "$ENGINE/../01、投递箱"/*.epub(N); do
    [ -e "$f" ] || continue
    name="$(basename "$f")"
    mkdir -p "$EPUB_SRC"
    # 源文件铁律：epub 只放 0000、原始文件/（同名跳过不覆盖）
    if [ -f "$EPUB_SRC/$name" ]; then
      echo "  ⊘ epub 源已在 0000、原始文件/，跳过重复：$name"
      rm -f "$f"
      continue
    fi
    mv "$f" "$EPUB_SRC/$name"
    echo "  → 源移入 0000、原始文件/$name"
    report epub "$name" running "拆解中"
    # ★ 拆解目录名来自 epub 内部书名（dc:title），**不是文件名**——
    #   如「川沐笔记 重制版.epub」拆出的是「川沐笔记/」。
    #   所以先从 split 输出里抓「书名: XXX」拿真实目录名，别用 basename 硬拼（2026-10-17 踩过）。
    split_out="$(bash "$EPUB_SPLIT" "$EPUB_SRC/$name" 2>&1)"
    if [ $? -eq 0 ]; then
      echo "  ✓ 已拆解：$name"
      real_title="$(printf '%s\n' "$split_out" | sed -n 's/^书名: //p' | head -1)"
      if [ -z "$real_title" ]; then
        real_title="$(basename "$name" .epub)"     # 兜底：抓不到书名就用文件名
      fi
      book_dir="$FACTORY/02、文稿成品线/04、EPUB拆解/$real_title"
      echo "  · 拆解目录：$(basename "$book_dir")"
      # 推 Notion：拆外链版 → push → 删临时外链目录
      if [ -f "$EPUB_PUSH" ]; then
        # ★ 实时透传 push 输出（不要 >/dev/null）——否则逐章推送静默，看着像「没反应」
        if bash "$EPUB_SPLIT" "$EPUB_SRC/$name" --subdir "_notion外链" >/dev/null 2>&1; then
          echo "  → 开始推 Notion（共 40 章，逐章显示进度）…"
          if bash "$EPUB_PUSH" "$book_dir/_notion外链" --parent "$EPUB_PARENT"; then
            echo "  ✓ 已推 Notion：$name"
            rm -rf "$book_dir/_notion外链"
            report epub "$name" ok "已拆解并推 Notion"
          else
            echo "  ✗ 推 Notion 失败（拆解已保留）：$name"
            echo "     外链版保留在：$book_dir/_notion外链（可重跑 push.sh --continue 续传）"
            report epub "$name" fail "推 Notion 失败（拆解已保留）"
          fi
        else
          echo "  ✗ 拆外链失败（拆解已保留）：$name"
          report epub "$name" fail "拆外链失败"
        fi
      else
        report epub "$name" ok "已拆解（未推 Notion，缺 push.sh）"
      fi
      n=$((n+1))
    else
      echo "  ✗ 拆解失败：$name"
      printf '%s\n' "$split_out" | tail -5
      report epub "$name" fail "拆解失败"
    fi
  done
  echo "  EPUB 拆解新出 $n 本。"
else
  echo "  （epub-to-xhtml skill 不存在，跳过）"
fi
echo

# ═══ ② 文稿线 ═══
echo "──────────────── ② 文稿线（html / zip / epub）────────────────"
cd "$ENGINE"
# ingest.py 统一入料，吃三种微信 HTML 形态 + Notion 导出 + ZIP，最终产出同一种 XHTML：
#   ① SingleFile 导出（var ct 时间戳）② Single HTML download「另存为」（base href + publish_time 可见日期）
#   ③ Notion 导出（属性表）——日期/作者都能解析，落 02、定稿库/<平台>/<作者>/xhtml/YYYY年M月/。
"$PY" ingest.py
echo

# ═══ ②⁺ 作者目录归一（六镇→九边、龙虎榜三板斧→猫笔刀、含 tony/xtony→xtony）═══
echo "──────────────── ②⁺ 作者目录归一 ────────────────"
report article "号名归并" running "扫描归并中"
"$PY" merge_accounts.py
report article "号名归并" ok "已归并（六镇→九边等）"
echo

# ═══ ③ 成人线 ═══
echo "──────────────── ③ 成人线（CSV）────────────────"
ADULT="$HOME/.workbuddy/skills/edge-batch-capture/scripts/intake_dispatch.py"
if [ -f "$ADULT" ]; then
  report adult "成人线分发" running "扫描投递箱 CSV"
  "$PY" "$ADULT" 2>&1 | tail -20
  report adult "成人线分发" ok "已分发"
else
  echo "  （成人线脚本不存在，跳过）"
  report adult "成人线分发" skip "脚本不存在"
fi
echo

# ═══ ④ 写真线 ═══
echo "──────────────── ④ 写真线（图片文件夹）────────────────"
PHOTO="$HOME/.workbuddy/skills/photobook-epub/scripts/make_epub.py"
PHOTO_BOX="$FACTORY/04、写真集线/01、投递箱"
PHOTO_OUT="$FACTORY/04、写真集线/02、成书"
if [ -d "$PHOTO_BOX" ]; then
  n=0
  for d in "$PHOTO_BOX"/*/(N); do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    out="$PHOTO_OUT/$name.epub"
    if [ -f "$out" ]; then
      continue
    fi
    report photo "$name" running "出书中"
    "$PY" "$PHOTO" --input "$d" --output "$out" --title "$name" --sequencer "沪上陈少" >/dev/null 2>&1
    if [ -f "$out" ]; then
      echo "  ✓ 写真集：$name"
      report photo "$name" ok "已出书"
      n=$((n+1))
    else
      echo "  ✗ 写真集失败：$name"
      report photo "$name" fail "出书失败"
    fi
  done
  echo "  写真线新出 $n 本。"
else
  echo "  （写真线投递箱不存在，跳过）"
  report photo "写真线" skip "投递箱不存在"
fi
echo

# ═══ ⑤ 定时任务 + 看门狗（首次装）═══
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

# ═══ ⑥ 刷新看板 + 推 GitHub ═══
echo "──────────────── ⑤ 刷新看板 + 推 GitHub ────────────────"
report finish "刷新看板" running "刷新中"
"$PY" "$HOME/.workbuddy/skills/dashboard-hub/scripts/dashboard.py" 2>&1 | tail -3
report finish "刷新看板" ok "看板已刷新"

BOARD_DIR="$FACTORY/00、看板中枢"
if [ -d "$BOARD_DIR/.git" ]; then
  cd "$BOARD_DIR"
  git add -A
  git -c user.name="Jessper2024" -c user.email="jessper@users.noreply.github.com" \
      commit -q -m "刷新看板 $(date '+%Y-%m-%d %H:%M:%S')" 2>/dev/null || true
  report finish "推 GitHub" running "推送中"
  if git -c http.proxy= -c https.proxy= -c http.version=HTTP/1.1 push origin main 2>&1 | tail -1; then
    report finish "推 GitHub" ok "已推送"
  else
    report finish "推 GitHub" fail "推送失败（网络）"
  fi
fi
echo

echo "════════════════════════════════════════════════════"
echo "  ✓ 全部处理完成。进度页：http://127.0.0.1:$PORT/"
echo "════════════════════════════════════════════════════"
echo
echo "按回车关闭本窗口…（进度页仍在后台，可随时刷新）"
read
