# EPUB 工厂

把微信公众号文章做成可长期收藏的 EPUB 文集。
流程：浏览器存的网页快照 → 单篇 XHTML → 按公众号分本、按月分章的合订 EPUB。

2026-09-16 从 `~/WorkBuddy/<时间戳>/` 和 `~/Documents/Codex/2026-09-09/ze/` 收拢到这里。
**这里是唯一的代码与规则所在地**，会话工作区只是临时沙盘，别再往里放长期资产。

## 目录

```text
~/Life/EPUB制作/              项目根（数据）
├─ _engine/                   本目录：代码
│   ├─ 成书.command           双击就跑（收件箱 → 成书 → 体检 → 回归）
│   ├─ 看板.command           双击就开看板（没在跑会后台起服务）
│   ├─ epub.sh                唯一入口
│   ├─ dashboard.py           常驻看板服务（本地 127.0.0.1:8760）
│   ├─ dash_service.sh        看板启停/状态/登录自启
│   ├─ report.py              报表页面生成（看板与 `_报表.html` 共用）
│   ├─ build_epub.py          合订主脚本（识别博主 → 分册/分章 → 三级目录 → 打包 → 报告 → 台账）
│   ├─ check_epub.py          交付前体检
│   ├─ regress.py             回归比对（防改坏）
│   ├─ dump_toc.py            打印三级目录树
│   ├─ sigi_convert.py        HTML → Sigil XHTML（已打微信懒加载补丁）
│   ├─ batch_wechat_sigil.py  批量转换
│   ├─ wechat_to_sigil_xhtml.py  自写转换器，回退用
│   ├─ yuanchuan_crawler/     微信列表抓取（备选）
│   ├─ backup.sh              打包源（xhtml + 原始HTML）
│   ├─ baselines.json         回归基线
│   ├─ README.md / PROJECT_NOTES.md   用法与项目约定
│   └─ .venv/                 自建虚拟环境（lxml + Pillow + requests）
├─ _处理报告.md                上次跑了什么（自动生成）
├─ _台账.md                    所有书的一览（自动生成）
├─ _待处理/                    收件箱：要处理的 HTML 丢这里（也会自动吸入下面两个目录）
├─ <公众号名>/
│   ├─ xhtml/                 该号全部 -Sigil.xhtml（不可删，EPUB 由它重建）
│   ├─ 原始HTML/              转换前的原 HTML 备份
│   ├─ cover.jpg              封面（可换，脚本不覆盖已有）
│   └─ <号名>_YYYYMM-YYYYMM.epub
└─ 微信公众号下载/            浏览器存 HTML 的源目录（自动吸入，不当号目录）
```

配套 skill：`~/.workbuddy/skills/epub-factory/`（用户级，任何会话自动可见）。

## 常用命令

```bash
cd ~/Life/EPUB制作/_engine

./epub.sh                 # 全部公众号重建
./epub.sh only 猫刀笔      # 只重建某一本
./epub.sh inbox           # 扫描 _待处理/，按博主分流并重建受影响的书
./epub.sh add a.html      # 归档指定文件并重建（原文件保留）
./epub.sh list            # 只看扫描结果
./epub.sh check           # 交付前体检：源 xhtml + EPUB 双向校验
./epub.sh report          # 生成一次报表页面 _报表.html（总览/各号/全部文章/推广图）
./epub.sh dash            # 开看板：http://127.0.0.1:8760（没在跑就起服务）
./epub.sh dash status     # 看板在不在跑
./epub.sh dash install    # 装成登录自启（开机就有，看板常驻）
./epub.sh dash stop       # 停掉看板
./epub.sh dash uninstall  # 取消登录自启
./epub.sh test            # 回归：和基线比对，看有没有改坏
./epub.sh test --save     # 确认改动是有意的，重存基线
./epub.sh split           # 按年分册（书太大时用）
./epub.sh toc 猫刀笔/猫刀笔_202608-202609.epub   # 打印三级目录树
```

**不想碰终端**：双击 `成书.command`（跑完自动打开项目文件夹）；想看运行情况就双击 `看板.command`。

`epub.sh` 自动用 `_engine/.venv`；找不到才回退系统 `python3`。

## 看板：随时看运行情况

打开 http://127.0.0.1:8760/ 就是项目当前状态（双击 `看板.command` 最快，会自动开浏览器）。
页面**不会自己重扫磁盘**——点「立即刷新」或按 F5 才重新扫描，按需看就行（扫描结果缓存 5 分钟）。
想让它定时自动刷，启动时加 `--refresh 60`。服务只监听本机，数据不出网。

看板与 `_报表.html` 由同一个 `report.py` 渲染，区别是**看板按需重扫**（点一下就是最新数据）：
总览数字、健康状态、月度柱图、各号卡片（封面/篇数/体积/月度分布）、全部文章可搜索表格、
推广图三类名单、最近动态、最近一次处理报告。

改了 `promo.py` / `build_epub.py` / `report.py` **不用重启看板**，它会自己热重载
（服务日志里会打「检测到脚本改动，已热重载」）。只有改 `dashboard.py` 本身才需要重启。

想要开机就有（不必每次双击）：

```bash
./epub.sh dash install     # 装成登录自启
./epub.sh dash status      # 看状态
./epub.sh dash uninstall   # 取消
```

`install` 要在**普通终端**里跑（launchd 在受限/沙箱环境下会被拒）。不装也行——
要看的时候双击 `看板.command`；服务是脱离终端跑的，关掉那个窗口它还在。

## 每次运行都会留痕

- `_报表.html`：**总览仪表盘**——文章总数、HTML 源数、EPUB 本数、体积、字数、配图、小标题，
  健康状态（回归/推广图/缺源），每月成文量柱图，各号卡片（含封面、epub 明细、月度分布），
  全部文章可搜索表格，推广图三类名单，最近一次处理报告。每次运行自动刷新，离线可看。
- `_处理报告.md`：这次新增了哪些、跳过重复哪些、失败哪些、删了几张推广图、各书篇数/月份/目录条目/体积。
- `_台账.md`：所有书的一览（篇数、时间跨度、体积、目录条目、最后更新），随时能看清家底。
- `baselines.json` + `./epub.sh test`：回归基线。改了规则先跑它，篇数/目录条目/体积对不上会直接报出来，
  确认是刻意改动就 `test --save` 重存。**长期项目最怕改坏不知道，这是保险丝。**

## 收件箱会自动吸入

`./epub.sh inbox` 会先去这些目录把新 HTML 吸进 `_待处理/` 再处理（原文件保留，已备份过的自动跳过）：

- `~/Downloads/微信公众号下载`（OpenClaw / SingleFile 的输出）
- `~/Life/EPUB制作/微信公众号下载`

想加新来源：改 `build_epub.py` 里的 `EXTRA_SOURCES`。

## 坏了怎么办

- 单篇转换失败不会中断整批，原因记进 `_处理报告.md` 的「失败」段。
- 改崩了：`git log` 看改动，`git checkout -- 文件` 回退；数据不在版本库里，不会被回退掉。
- 数据丢了：`./backup.sh [目标目录]` 打包各号的 `xhtml/` + `原始HTML/`（成品 epub 可从源重建，不进包）。

## 规则速查

- **目录层级**：h1 = 月份，h2 = 「日期：标题」，h3 = 文内小标题；从旧到新排序；三级目录 nav + ncx 都写。
- **小标题判定只有一处**：`normalize_headings()`，被 `parse_article()`（合订）和 `tidy_sources()`
  （写回源文件）共用——**改标题规则只改这里**。规则：h1/h2 → h3，空的/超 40 字/以 `[` 开头的
  （参考文献）/图注降回 `<p>`；漏判的序号小标题（`<p>01</p>`）抬成 h3。
- **源文件也要规范**：打包前 `tidy_sources()` 就地改写 `xhtml/`，让 Sigil 里打开的源文件和
  合订出的 EPUB 目录**完全一致**（幂等，重复跑 0 改动）。
- **元数据**：`dc:creator` = 公众号博主名，排序作者恒为「沪上陈少」。
- **去推广图（会自己学）**：`promo.py` 给每篇的图片记账；跨文章重复出现在文末的自动升为候选，
  人工看图确认后永久删除（`./epub.sh ads` 看名单，`--promote-all` 转正）。
  作者配图（如猫刀笔文末生活照）标「已排除」永不删。历史补记账用 `./epub.sh backfill`。
- **成书门槛**：一个号攒够 **30 篇** XHTML 才出书（`--min-articles N` 可改，`0` = 关掉门槛）。
  **已有成品的号不受门槛约束**，照常跟着新增更新——否则书会停在旧版本、界面上还看不出异样。
  只管"还没成过书"的号：篇数不够就不建，等攒够；想提前出书就 `./epub.sh only 号名`
  （**手动点名一律无视门槛**）。被挡下的会写明「暂未成书 · 源 N 篇，差 M 篇」，
  处理报告 / 台账 / 看板三处都能看到。
- **号名识别**：文件名 `日期_号_作者_标题` → meta 行 → `#js_name`；目录匹配走 `resolve_dir()` 模糊匹配
  （号名「猫笔刀」≠ 目录「猫刀笔」）。
- 详细规则与踩坑见 skill 的 `references/operations.md` 和本目录 `PROJECT_NOTES.md`。

## GitHub 私有仓库

代码备份在 https://github.com/Jessper2024/epub-factory（**private**，只有账号主人可见）。

- 只放代码与文档，**书籍数据一律不上传**：`xhtml/`、`原始HTML/`、`*.epub`、封面都在仓库之外
  （文章是他人作品，有版权顾虑，体积也大，不适合进版本库）。
- 改动后同步：`./epub.sh sync`（自动 commit + push）。
- 换电脑取回：`git clone git@github.com:Jessper2024/epub-factory.git _engine`，再 `pip install lxml Pillow requests`。

## 依赖

```bash
.venv/bin/pip install lxml Pillow requests
```

重建虚拟环境（换机或 venv 坏了）：

```bash
/usr/bin/python3 -m venv .venv || python3 -m venv .venv
.venv/bin/pip install lxml Pillow requests
```

## 备份

真正不可再生的是各号目录下的 `xhtml/` 和 `原始HTML/`（EPUB 可从它们重建）。
整个 `~/Life/EPUB制作` 约 137M，一个文件夹打包即可。
