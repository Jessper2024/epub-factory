# EPUB 工厂

把微信公众号文章做成可长期收藏的 EPUB 文集。
流程：浏览器存的网页快照 → 单篇 XHTML → 按公众号分本、按月分章的合订 EPUB。

2026-09-16 从 `~/WorkBuddy/<时间戳>/` 和 `~/Documents/Codex/2026-09-09/ze/` 收拢到这里。
**这里是唯一的代码与规则所在地**，会话工作区只是临时沙盘，别再往里放长期资产。

## 目录

```text
~/Life/EPUB制作/              项目根（数据）
├─ _engine/                   本目录：代码
│   ├─ epub.sh                唯一入口
│   ├─ build_epub.py          合订主脚本（识别博主 → 按月分章 → 三级目录 → 打包）
│   ├─ check_epub.py          交付前体检
│   ├─ dump_toc.py            打印三级目录树
│   ├─ sigi_convert.py        HTML → Sigil XHTML（已打微信懒加载补丁）
│   ├─ batch_wechat_sigil.py  批量转换
│   ├─ wechat_to_sigil_xhtml.py  自写转换器，回退用
│   ├─ yuanchuan_crawler/     微信列表抓取（备选）
│   ├─ PROJECT_NOTES.md       项目约定与踩坑记录
│   └─ .venv/                 自建虚拟环境（lxml + Pillow + requests）
├─ _待处理/                   收件箱：要处理的 HTML 丢这里
├─ <公众号名>/
│   ├─ xhtml/                 该号全部 -Sigil.xhtml（不可删，EPUB 由它重建）
│   ├─ 原始HTML/              转换前的原 HTML 备份
│   ├─ cover.jpg              封面（可换，脚本不覆盖已有）
│   └─ <号名>_YYYYMM-YYYYMM.epub
└─ 微信公众号下载/            源文件夹（只当收件箱用，不当号目录）
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
./epub.sh toc 猫刀笔/猫刀笔_202608-202609.epub   # 打印三级目录树
```

`epub.sh` 自动用 `_engine/.venv`；找不到才回退系统 `python3`。

## 规则速查

- **目录层级**：h1 = 月份，h2 = 「日期：标题」，h3 = 文内小标题；从旧到新排序；三级目录 nav + ncx 都写。
- **源文件也要规范**：打包前 `tidy_sources()` 就地改写 `xhtml/`，标题 h1 唯一，真小标题 h3，
  空/超 40 字/以 `[` 开头（参考文献）/图注一律降 `<p>`。合订侧 h3 不再降级。
- **元数据**：`dc:creator` = 公众号博主名，排序作者恒为「沪上陈少」。
- **去推广图**：`build_epub.py` 里的 `PROMO_FILE_IDS`（戴老板 8 个 fileid，视觉确认过），转换前按 URL 删。
- **号名识别**：文件名 `日期_号_作者_标题` → meta 行 → `#js_name`；目录匹配走 `resolve_dir()` 模糊匹配
  （号名「猫笔刀」≠ 目录「猫刀笔」）。
- 详细规则与踩坑见 skill 的 `references/operations.md` 和本目录 `PROJECT_NOTES.md`。

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
