# 远川研究所 2024–2026 公众号原文 → EPUB 合订

目录里有两个脚本，对应两种分工。先说结论：**推荐用方案 A**。

- **A. `html_to_epub.py`** —— 你本机把公众号文章「微信原文 HTML」导出到 `raw_html/`，
  我（服务端）负责扫描这些 HTML、按 标题/日期/正文 提取并合成一本 EPUB。
  **不需要任何登录态**，最干净。本文档重点写这个。
- **B. `yuanchuan_crawler.py`** —— 一键抓列表+正文+EPUB，但「列表抓取」必须本机带
  微信登录态跑（见文末「备选方案 B」）。仅在你嫌导出 HTML 麻烦时再用。

---

## 方案 A：你导出 HTML，我合成 EPUB（推荐）

### 第一步：本机导出微信原文 HTML

打开公众号任意一篇文章，**在浏览器里**（Chrome / Safari 都行，但推荐 Chrome）：

- 菜单 → **「网页，完整」另存为**：会生成 `xxx.html` + 同名 `xxx_files/` 图片夹。
  👉 把 `xxx.html` 和 `xxx_files/` **一起**丢进 `raw_html/` 即可，图片自动内嵌。
- 或者只存单个 `xxx.html`（无图片夹）：正文里图片走远程 `data-src`，合成时会自动
  下载进 EPUB（需要服务端能访问微信图床 mmbiz.qpic.cn）。

> 想省事批量导出？Chrome 装个「批量链接另存」类扩展，或微信 PC 客户端里把文章
> 逐篇「在浏览器打开」再另存。导出数量无上限，丢多少合成多少。

### 第二步：把文件放进 raw_html/

```
yuanchuan_crawler/
└── raw_html/                # ← 你导出的全部 .html 放这里（可嵌套子目录）
    ├── 2024-01-文章A.html
    ├── 2024-01-文章A_files/   # 若用「网页，完整」，同名图夹一起放
    ├── 2024-03-文章B.html
    └── ...
```

- 支持任意层级 `*.html` / `*.htm`，脚本会递归扫描。
- 重复文章按 `sn`（链接里的唯一 id）或「标题+日期」自动去重。

### 第三步：合成 EPUB

服务端（一次性装依赖）：

```bash
/Users/jessper/.workbuddy/binaries/python/envs/default/bin/python3 -m pip install lxml
```

合成：

```bash
cd /Users/jessper/WorkBuddy/2026-09-16-04-04-48/yuanchuan_crawler
python3 html_to_epub.py                 # 扫描 raw_html/ → 远川研究所_2024-2026.epub
python3 html_to_epub.py --no-images     # 不内嵌图片（EPUB 体积小，图片仍引用远程链接）
python3 html_to_epub.py --out my.epub    # 指定产物名
python3 html_to_epub.py --raw ./别的目录 # 指定别的 HTML 源目录
```

产物：`远川研究所_2024-2026.epub`（按日期正序，每篇一章，图片内嵌），
可直接导入微信读书 / Kindle / Apple Books。处理明细见 `raw_html/processing_log.csv`。

### 提取规则（已验证）
- **标题**：`<meta property="og:title">` → 回退 `#activity-name` → `<title>`
- **日期**：优先 `var ct = "1705..."` 发布时间戳（最准）；回退 `<span id="publish_time">`
- **正文**：`<div id="js_content">` 内全部内容
- **图片**：`data-src`（微信真图，多为 CDN 远程，下载内嵌）；若无则回退同标签 `src`
  指向的本地 `_files/` 文件（Chrome「网页，完整」另存即有）。远程下载失败会自动落到本地图。
- **正文清洗**：用 `lxml` 把宽松 HTML 洗成良构 XHTML（自闭 `<br>/<img>`、转义 `&`、
  收拢未闭合自定义标签），保证 EPUB 在主流阅读器里能正常打开。

---

## 备选方案 B：yuanchuan_crawler.py（需微信登录态）

列表抓取依赖你的微信登录态，所以要在你本机、用已登录微信网页版的 Chrome 跑：

```bash
pip3 install playwright && playwright install chromium
python3 yuanchuan_crawler.py            # 一键：列表+正文+EPUB（会弹 Chrome，别关）
python3 yuanchuan_crawler.py --list     # 只抓列表
python3 yuanchuan_crawler.py --content  # 只抓正文
python3 yuanchuan_crawler.py --epub      # 只合成
```

**前提**：Chrome 已登录 https://wx.qq.com；运行前关掉普通 Chrome 窗口（同用户目录会被锁）。
列表只命中几篇 / `appmsg_token` 为空 → 登录态过期，重登后重跑。

---

## 常见问题
- **EPUB 太大** → 加 `--no-images`；或抓完用 calibre 压缩图片。
- **某篇图片缺了几张** → 日志里会标「图无法获取」，通常是微信图床防盗链；可本机用
  「网页，完整」另存（图在本地 `_files/`）再丢进来，走本地回退。
- **想换年份区间** → 改 `html_to_epub.py` 顶部 `YEAR_START/YEAR_END`（目前 2024–2026）。
- **多平台聚合退路** → 远川在 虎嗅/36氪/创业邦/观察者网/品玩/腾讯网 都有官方同步，
  内容等同原文但非 mp 域名。需要服务端直接做可说「切多平台聚合」。
