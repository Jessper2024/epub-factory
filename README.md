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
│   ├─ 看板.command           双击就开本地看板（没在跑会后台起服务）
│   ├─ epub.sh                唯一入口
│   ├─ build_epub.py          合订主脚本（识别博主 → 分册/分章 → 三级目录 → 打包 → 报告 → 台账）
│   ├─ sigi_convert.py        HTML → Sigil XHTML（已打微信懒加载补丁）
│   ├─ batch_wechat_sigil.py  批量转换
│   ├─ inbox_auto.sh          定时收文（launchd 每小时；install/status/run）
│   ├─ cloud_publish.sh       云端看板发布（GitHub Pages；publish/install/status）
│   ├─ dashboard.py           本地看板服务（127.0.0.1:8760）
│   ├─ dash_service.sh        看板启停/状态/登录自启
│   ├─ report.py              报表渲染（本地看板、云端看板、_报表.html 共用）
│   ├─ check_epub.py          交付前体检
│   ├─ regress.py             回归比对（防改坏）
│   ├─ dump_toc.py            打印三级目录树
│   ├─ promo.py               推广图黑名单（会自己学）
│   ├─ wechat_to_sigil_xhtml.py  自写转换器，回退用
│   ├─ yuanchuan_crawler/     微信列表抓取（备选）
│   ├─ backup.sh              打包源（xhtml + 原始HTML）
│   ├─ core/                  架构基础层（配置/审计/护栏/自检/可扩展管线）
│   │   ├─ config.py          集中配置（常量的单一真相源）
│   │   ├─ audit.py           审计日志（_审计/YYYY-MM.jsonl，可追溯）
│   │   ├─ safety.py          原子写 + 不可再生目录护栏
│   │   ├─ health.py          系统自检（8 项 + 评分）
│   │   ├─ pipeline.py        来源协议与注册表（扩展新类型用）
│   │   └─ adapters/          wechat（新增来源类型就加一个文件）
│   ├─ baselines.json         回归基线
│   ├─ allowed_accounts.json  新号白名单（本机状态，不进版本库）
│   ├─ config.local.json      本地覆盖配置（可选，不进版本库）
│   ├─ index.html / .nojekyll 云端看板产物（自动维护，勿手改）
│   ├─ README.md / PROJECT_NOTES.md / ARCHITECTURE.md   用法·约定·架构
│   └─ .venv/                 自建虚拟环境（lxml + Pillow + requests）
├─ _处理报告.md                上次跑了什么（自动生成）
├─ _处理报告_预演.md           上次 --dry-run 报告（不覆盖上面那份）
├─ _台账.md                    所有书的一览（自动生成）
├─ _报表.html                  报表本体（云端看板的数据源）
├─ _已归档指纹.json            判重指纹表（自动生成，删了会重建）
├─ _待处理/                    收件箱：要处理的 HTML 丢这里
├─ _待确认新号/<号名>/         名单外的新号先搁这儿，点名（allow）后才收
├─ <公众号名>/
│   ├─ xhtml/                 该号全部 -Sigil.xhtml（不可删，EPUB 由它重建）
│   ├─ 原始HTML/              转换前的原 HTML 备份
│   ├─ cover.jpg              封面（可换，脚本不覆盖已有）
│   └─ <号名>_YYYYMM-YYYYMM.epub
└─ 微信公众号下载/            旧位置，已不用（现在只盯 ~/Life/01、源料_微信公众号下载）
```

配套 skill：`~/.workbuddy/skills/epub-factory/`（用户级，任何会话自动可见；详细规则的唯一出处是它的
`references/operations.md`）。

## 常用命令

```bash
cd ~/Life/EPUB制作/_engine

./epub.sh                 # 全部公众号重建
./epub.sh only 猫刀笔      # 只重建某一本（手动点名，无视成书门槛）
./epub.sh inbox           # 吸入源料文件夹 + 扫描 _待处理/，按博主分流并重建
./epub.sh inbox --dry-run # 预演：只说会收什么、跳过什么，一份文件都不落地
./epub.sh allow 晚点LatePost  # 放行一个新号（把 _待确认新号/ 里搁置的文件放回收件箱）
./epub.sh auto install    # 装定时收文：每小时扫一次源料文件夹
./epub.sh add a.html      # 归档指定文件并重建（原文件保留）
./epub.sh list            # 只看扫描结果
./epub.sh check           # 交付前体检：源 xhtml + EPUB 双向校验
./epub.sh test            # 回归：和基线比对，看有没有改坏（刻意改动用 test --save 重存）
./epub.sh split           # 按年分册（书太大时用）
./epub.sh toc 猫刀笔/猫刀笔_202608-202609.epub   # 打印三级目录树
./epub.sh report          # 生成一次报表页面 _报表.html
./epub.sh ads             # 看推广图黑名单（--promote-all 转正候选）
./epub.sh backfill        # 给已归档 HTML 补记图片账
./epub.sh sync            # 代码改动提交并推 GitHub

# 自检与备份
./epub.sh health          # 系统自检：依赖/磁盘/git/备份/结构/异常，给评分
./epub.sh backup          # 备份不可再生资产（3-2-1：本地 + 异地副本）
./epub.sh backup list     # 列现有备份（含年龄与校验状态）
./epub.sh backup verify <包>   # 校验某一份备份能不能用来恢复

# 本地看板（本机看）
./epub.sh dash            # 开 http://127.0.0.1:8760（没在跑就起服务）
./epub.sh dash status     # 在不在跑
./epub.sh dash install    # 装成登录自启

# 云端看板（手机/任意电脑看）
./epub.sh cloud --force   # 立即发布到 GitHub Pages
./epub.sh cloud status    # 看上次发布时间
./epub.sh cloud install   # 装定时：每 4 小时自动发布
```

**不想碰终端**：双击 `成书.command`（跑完自动打开项目文件夹）；
看本地状态双击 `看板.command`；手动刷新云端看板双击桌面的 `发布云端看板.command`。

`epub.sh` 自动用 `_engine/.venv`；找不到才回退系统 `python3`。

## 两个看板

### 本地看板：http://127.0.0.1:8760

打开就是项目当前状态（双击 `看板.command` 最快，会自动开浏览器）。
页面**不会自己重扫磁盘**——点「立即刷新」或按 F5 才重新扫描（扫描结果缓存 5 分钟）。
想让它定时自动刷，启动时加 `--refresh 60`。服务只监听本机，数据不出网。

改了 `promo.py` / `build_epub.py` / `report.py` **不用重启看板**（自动热重载）。
只有改 `dashboard.py` 本身才需要重启。

### 云端看板：https://jessper2024.github.io/epub-factory/

手机、平板、任意电脑浏览器直接开，无需登录。内容与本地看板同源（同一个 `report.py` 渲染）。

- 装了 `./epub.sh cloud install` 后**每 4 小时自动发布**；
- 想立即刷新：跑 `./epub.sh cloud --force`，或双击桌面 `发布云端看板.command`；
- 仓库已改 public（陈少确认无敏感数据），Pages 托管，零成本。

## 每次运行都会留痕

- `_报表.html`：**总览仪表盘**——文章总数、HTML 源数、EPUB 本数、体积、字数、配图、小标题，
  健康状态（回归/推广图/缺源），每月成文量柱图，各号卡片（含封面、epub 明细、月度分布），
  全部文章可搜索表格，推广图三类名单，最近一次处理报告。每次运行自动刷新，离线可看。
- `_处理报告.md`：这次新增了哪些、跳过重复哪些、失败哪些、删了几张推广图、各书篇数/月份/目录条目/体积。
- `_台账.md`：所有书的一览（篇数、时间跨度、体积、目录条目、最后更新），随时能看清家底。
- `baselines.json` + `./epub.sh test`：回归基线。改了规则先跑它，篇数/目录条目/体积对不上会直接报出来，
  确认是刻意改动就 `test --save` 重存。**长期项目最怕改坏不知道，这是保险丝。**

## 只盯一个源料文件夹（每小时自动收）

**只盯这一个文件夹**，别的地方一律不碰（陈少 2026-09-16 下午定）：

- `/Users/jessper/Life/01、源料_微信公众号下载`（旧的 `~/Downloads/微信公众号下载` 已停用）

`./epub.sh inbox` 会先把这里的新 HTML 吸进 `_待处理/` 再处理；**原文件不动**（复制，不是搬走）。
子目录也认（默认往下 1 层）。想加来源：改 `build_epub.py` 的 `EXTRA_SOURCES`。

### 装成每小时自动跑（一次就够）

```bash
cd ~/Life/EPUB制作/_engine
./epub.sh auto install     # 装：每小时扫一次
./epub.sh auto status      # 看状态 + 上次跑了什么
./epub.sh auto run         # 立刻手动跑一次
./epub.sh auto uninstall   # 取消
```

装好后**不依赖 WorkBuddy 是否开着**，纯脚本、零消耗；日志在 `~/Library/Logs/epub-inbox.log`。
> 受限环境（沙箱 / 非本人终端）里 `launchctl` 会被拒，**要在自己的终端里跑 install**。
> 想改频率：`INBOX_INTERVAL=7200 ./epub.sh auto install`（秒）。

### 三条防呆（都是踩出来的）

- **重复副本按内容认，不按文件名**。同一篇换个名字再存一遍（`01_闯祸了.html` 这种）靠
  「文章 ID（`og:url` 里的 `sn`）+ 标题·发布时刻」双指纹识别，命中任一即跳过。
  只按文件名判重会让猫刀笔从 19 篇涨到 32 篇。
- **新号要走一次点名**。识别出的号不在白名单里时，文件先搁到 `_待确认新号/<号名>/`，
  **不建书、也绝不混进别人的书**；要收就 `./epub.sh allow <号名>`（会自动把搁置的文件放回收件箱），
  不要就不管。看板「待确认新号」卡会一直提醒。
- **先预演再动手**。`./epub.sh inbox --dry-run` 只报告会收什么、跳过什么，**一份文件都不落地**，
  报告另存 `_处理报告_预演.md`（不覆盖上次正式执行的留痕）。

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

## 架构与扩展（想改代码先看这个）

系统分四层，**依赖永远单向：业务 → 扩展 → 基础**，基础层绝不反向 import 业务脚本：

| 层 | 内容 |
|---|---|
| 入口层 | `epub.sh`（唯一入口）· `inbox_auto.sh` · `cloud_publish.sh` · `.command` 双击入口 |
| 业务层 | `build_epub.py`（合订核心，1533 行，**已验证正确，不重写**）· `sigi_convert.py` · `promo.py` · `regress.py` |
| 扩展层 | `core/pipeline.py` + `core/adapters/`（新增来源类型只加一个文件） |
| 基础层 | `core/config.py` 配置 · `core/audit.py` 审计 · `core/safety.py` 护栏 · `core/health.py` 自检 |

**新增一种来源类型**（网页正文、RSS、PDF…）三步，不动主流程：

1. 新建 `core/adapters/mykind.py`，实现 `identify / extract / render` 并 `register(...)`
2. 在 `core/adapters/__init__.py` 的 `_ADAPTERS` 里加上名字
3. 试转：`python3 -m core.pipeline <文件> <输出目录>`

规则不要复制：正文清洗、图片还原留在 `sigi_convert.py`，adapter 只做调度。

完整说明（分层图、8 条不变量、灾难恢复手册）见 **`ARCHITECTURE.md`**。

## GitHub 与云端

仓库：https://github.com/Jessper2024/epub-factory（**public**，陈少确认无敏感数据）。

- 只放代码与文档，**书籍数据一律不上传**：`xhtml/`、`原始HTML/`、`*.epub`、封面都在仓库之外
  （文章是他人作品，有版权顾虑，体积也大，不适合进版本库）。
- 例外：云端看板需要的 `index.html`（报表）和 `.nojekyll` 会进仓库——那是聚合统计，不含原文。
- 改动后同步：`./epub.sh sync`（自动 commit + push）。
- 换电脑取回：`git clone https://github.com/Jessper2024/epub-factory.git _engine`，
  再 `pip install lxml Pillow requests`（或重建 `.venv`）。

云端看板四个坑（Pages 404 排查用）：仓库必须 public（改 public 要带
`--accept-visibility-change-consequences`）、Pages 要单独启用、文件名必须 `index.html`（
下划线开头会被 Jekyll 吞）、要配 `.nojekyll`。详见 skill `references/operations.md`。

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
整个 `~/Life/EPUB制作` 约 140M，一个文件夹打包即可。
