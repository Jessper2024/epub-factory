# 架构说明

> 面向"长期无人值守、随时可扩展"的目标。
> 想知道怎么用看 `README.md`；想知道规则细节看 skill 的 `references/operations.md`；
> 想知道为什么这么定看 `PROJECT_NOTES.md`。本文件回答：**系统是怎么搭的、往哪扩、坏了怎么救**。

## 分层

```
┌─────────────────────────────────────────────────────────┐
│ 入口层   epub.sh（唯一入口）· 成书.command · 看板.command    │
│          inbox_auto.sh（收文定时）· cloud_publish.sh（云端）│
├─────────────────────────────────────────────────────────┤
│ 业务层   build_epub.py  合订主脚本（识别/归档/打包/报告）    │
│          sigi_convert.py  HTML → XHTML（正文与图片规则）   │
│          promo.py 推广图自学习 · regress.py 回归            │
│          check_epub.py 体检 · report.py 渲染 · dashboard.py│
├─────────────────────────────────────────────────────────┤
│ 扩展层   core/pipeline.py  来源协议 + 注册表                │
│          core/adapters/     wechat（新增类型就加一个文件）   │
├─────────────────────────────────────────────────────────┤
│ 基础层   core/config.py 配置 · core/audit.py 审计           │
│          core/safety.py 护栏 · core/health.py 自检          │
└─────────────────────────────────────────────────────────┘
                        ↓
数据层  ~/Life/EPUB制作/<号>/  xhtml/ · 原始HTML/ · cover.jpg · epub
```

**依赖方向永远是单向的：业务 → 扩展 → 基础**，基础层绝不反向 import 业务脚本
（否则循环依赖，且 import 业务模块会触发它的模块级副作用，如读白名单、读推广图账本）。
adapter 需要业务能力时，一律**函数内延迟导入**。

## 为什么这样分

| 层 | 解决的问题 | 若没有会怎样 |
|---|---|---|
| 入口层 | 一个命令干一件事，可定时、可双击 | 命令散落，忘了怎么跑 |
| 业务层 | 真正的成书逻辑（已验证正确，不动） | — |
| 扩展层 | 新增来源类型不改主流程 | 加一种类型就在主流程加 if-else，越加越脆 |
| 基础层 | 配置集中、操作可追溯、危险操作被拦、健康可观测 | 常量散落、出问题查不到、误删数据、静默失效 |

**最重要的一条：业务层 `build_epub.py`（1533 行）是验证过的核心，架构改造不重写它。**
所有新能力长在它外面，通过 import 使用。这是"加固而非重写"的原则——
重写一个正在正确运行的系统，风险远大于收益。

## 八条不变量（改了必出事）

1. 标题判定只有一处 `normalize_headings()`。
2. 已有成品的号不受 30 篇门槛约束（`publish_blocked()` 先查 `*.epub`）。
3. 手动点名（`--only`/`--add`/全量）一律无视门槛。
4. 文件名禁用 ASCII `%`（libxml2 把路径当 URI，读写不对称）。
5. 判重按内容指纹（双键），先算先登记再判"收过没有"。
6. GitHub 只推代码不推数据（例外：`index.html`+`.nojekyll`，是聚合统计）。
7. 目录格式：h1 月份 / h2 日期+标题 / h3 小标题，从旧到新，三级 nav+ncx。
8. 源文件与 EPUB 目录一致（打包前 `tidy_sources()` 就地规范，幂等）。

每条的背后都有一次真实事故，详见 `PROJECT_NOTES.md`。

## 怎么扩展：新增一种来源类型

三步，不动主流程、不改 build_epub.py：

1. 新建 `core/adapters/mykind.py`，实现四个方法：

```python
class MyKindAdapter:
    name = "mykind"
    def identify(self, path) -> bool:      # 这个文件是不是我这种来源
    def extract(self, path) -> RawDoc:     # 抽 title/date/author/account
    def render(self, doc, out_dir) -> Path # 渲染成 Sigil 可用的 XHTML

register(MyKindAdapter())
```

2. 在 `core/adapters/__init__.py` 的 `_ADAPTERS` 里加上 `"mykind"`。
3. 完事。`pipeline.pick(path)` 会自动认出来，`pipeline.run(path, out)` 就能转。

试转：`python3 -m core.pipeline <文件> <输出目录>`。

**规则不要复制**：正文清洗、图片还原等规则留在 sigi_convert.py，adapter 只负责调度。
规则一旦有两份，改一处漏一处是迟早的事（这条是历史教训）。

## 可观测性

| 看什么 | 在哪 |
|---|---|
| 每次运行干了什么 | `_处理报告.md`（人读）· `_审计/YYYY-MM.jsonl`（机读，append-only） |
| 家底 | `_台账.md` |
| 整体健康 | `core/health.py` → 看板「系统自检」卡（8 项检查 + 评分） |
| 改坏没有 | `./epub.sh test`（对比 `baselines.json`） |
| 最近异常 | `python3 -m core.audit failures` |

审计日志每条含：时间、事件、耗时、结果数字、当时代码版本（git short hash）。
出问题时能回答"最近一次正常是什么时候""这次改动之后有没有异常"。

## 定时任务（两个独立 launchd）

| 任务 | 命令 | 频率 | 干什么 |
|---|---|---|---|
| 收文 | `./epub.sh auto install` | 每小时 | 扫源料文件夹 → 判重 → 分流归档 → 重建受影响的书 |
| 云端看板 | `./epub.sh cloud install` | 每 4 小时 | 生成报表 → 复制成 index.html → push 到 Pages |

都必须在陈少本机终端安装（沙箱里 `launchctl` 被拒）。

## 灾难恢复手册

**先说结论：真正不可再生的只有各号的 `xhtml/` 与 `原始HTML/`。**
EPUB 能从源重建，封面能自动生成，`_审计/`、指纹表都能重建。

| 情形 | 恢复动作 |
|---|---|
| 误删单篇源 | 从 `原始HTML/` 重新转换；或从最近备份的 `xhtml/` 取回 |
| 误删整个号目录 | `./backup.sh list` 找最近一份 → `verify` 确认没坏 → 解包取回 |
| 改规则改崩了 | `git log` 找改动 → `git checkout -- 文件`；数据在仓库外，不受影响 |
| EPUB 坏了 | `./epub.sh only <号名>` 从源重建（源在就行） |
| 整盘/目录没了 | 用异地副本（3-2-1 的另外两份）恢复；所以**一定要配 backup_targets** |
| 备份坏了 | `verify-all` 会标出来；所以保留 5 份而不是 1 份 |

配置异地目标（3-2-1 的关键一步）：

```bash
.venv/bin/python -c "from core import config; config.save_local(backup_targets=['/Volumes/你的移动硬盘/EPUB备份'])"
```

**未配异地目标时，`health` 会持续提示"只有 1 份副本"**——这是故意的，不让它静默。

## 验收清单（每次改动后）

```bash
cd ~/Life/EPUB制作/_engine
./epub.sh check          # 源 + EPUB 双向体检，必须 0 问题
./epub.sh test           # 与基线比对，必须一致（刻意改动才 test --save）
.venv/bin/python -m core.health   # 系统自检，分数与各项
./epub.sh sync           # 推 GitHub
```

任一项不绿就不算完成。**check 报 0 但某本书没被扫到，等于没检查**——
历史上晚点LatePost 就因为源平铺在号目录根下被整本跳过，显示"0 问题"却根本没查（已修）。
