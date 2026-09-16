# 项目长期约定（微信文章抓取 → EPUB）

## 工作流（陈少 2026-09-16 拍板）
- 陈少给微信文章链接 → 我**直接解析处理**，不来回追问。
- 处理链：`fetch_links.py`（抓链接正文 HTML 落 raw_html/）→ `html_to_epub.py`（合成 EPUB）。
  - 单篇链接可直接当参数：`python3 fetch_links.py url1 url2 ...`
- 正文提取：标题 `og:title` / 日期 `var ct` 时间戳 / 正文 `<div id="js_content">`；`lxml` 洗成良构 XHTML。

## 微信风控规律（已实测，重要）
- **短链 `/s/CODE` 形式：服务端可抓**（移动端 UA + 自动跟重定向）。
- **长链 `/s?__biz=..&mid=..&sn=..&chksm=..` 形式：可能触发微信「环境异常」号级风控**
  —— 服务端 IP 被拦时返回验证页，urllib 与 WebFetch 通道都拿不到。是否被拦**按公众号 biz 而定**
  （远川 `MzIwMDY2NTgwMA==` 长链没事；这个号 `MzE5ODk2NjUwOA==` 长链被拦）。
- 被拦的长链**服务端硬下不来**，出路只有本机：浏览器「网页，完整」另存 HTML 丢 raw_html/，
  或本机跑 `fetch_links.py`（本机 IP 正常、可能带登录态，能过风控）。**不要盲目后台重试，会加重风控。**
- 待补清单：`yuanchuan_crawler/pending_links.txt`（9 个被拦长链）。

## 本地 HTML → Sigil XHTML（陈少主力流程，2026-09-16 定案）
- 唯一入口：**`/Users/jessper/Documents/Codex/2026-09-09/ze/outputs/sigi_convert.py`**（已打懒加载补丁，
  备份 `*.bak-20260916-064630`）。单篇 `sigi_convert.py in.html -o out.xhtml --type wechat`；
  整个目录用 `/Users/jessper/WorkBuddy/2026-09-16-04-04-48/batch_wechat_sigil.py`（改 SRC/OUT 即可）。
- 陈少用 OpenClaw（=SingleFile）在浏览器存微信文章，HTML 丢 `~/Downloads/微信公众号下载`；
  成品 XHTML 落 `~/Life/EPUB制作/<号名>/`。命名 `日期_号_作者_标题-Sigil.xhtml`。
- 我自写的 `wechat_to_sigil_xhtml.py` 已不主用（补丁并入 sigi_convert 后功能重叠），留作回退。
- 微信懒加载图：`src` 是 1×1 SVG 占位、真图在 `data-src`，必须下载内联；`mmbiz.qpic.cn` 要文章页 Referer。
  判定占位时**不能只看 class 含 placeholder**（嵌入后 class 不变会把真图又换回外链），详见今日日志。
- 验收：XML 可解析 + 占位图 0 + `<br>` 0 + 外链 0。

## XHTML → 合订 EPUB（2026-09-16 定案）
- 脚本：`build_epub.py`（本工作区）。`--only 号` / `--add 文件...`（自动识别号名归档+重建）/ `--list`。
- 约定：**每号一个独立文件夹** `~/Life/EPUB制作/<号名>/`：
  `xhtml/`（原始文档备份）+ `cover.jpg`（封面，自动生成、可换）+ `<号名>_YYYYMM[-YYYYMM].epub`（产物）。
  `--add` 新文件自动归到 `<号>/xhtml/`。
- **收件箱流程**：`~/Life/EPUB制作/_待处理/` 丢待处理 HTML，`build_epub.py --inbox` 扫描 → 按号分流 →
  转 XHTML 归档 + 原 HTML 备份到 `<号>/原始HTML/` → 只重建受影响的那几本 EPUB。新号自动建目录+封面。
  陈少另一个源文件夹 `EPUB制作/微信公众号下载/` 已加 SKIP_DIRS（只当收件箱用，不当号目录）。
- **去推广图**：`PROMO_FILE_IDS` 黑名单（戴老板 8 个 fileid，全部视觉确认），转换前按 URL 删；
  防重复：XHTML 目标名由内容（日期+号+作者+标题）生成，同名自动跳过——源文件夹清空后再放重复文件也能识别。
- **月份 = h1，文章标题 = h2（带日期前缀「2026年8月21日：死贵死贵的」），从旧到新**；
  每月一个 part，文内小标题降级 h3；目录页放 spine 首位；目录文字与正文 h2 一致。
- **目录是三级**：月份 → 文章 → h3 小标题（nav + ncx 都写，否则目录里看不到 01/02/03）。
  空标题、超 40 字、以 `[` 开头的（参考文献）、图注（图后 ≤20 字短块）一律降级回 `<p>` 不进目录。
- **源文件也要规范化**（陈少明确要求：Sigil 里打开源文件不能是错标题）：`build_book()` 打包前先跑
  `tidy_sources()` 就地改写 `<号>/xhtml/*.xhtml`——标题 h1 唯一不动，其余 h1/h2 按同一套判定 → 真小标题 **h3**、其余 `<p>`。
  合订侧对应改成 **h3 不再降 h4**（否则源文件规范后合订又降级，三级目录收不到）。幂等（跑第二次 changed=0）。
- 交付前必跑体检：`check_epub.py [号名...]`（源 xhtml + EPUB 双向校验）、`dump_toc.py <epub>`（打印三级目录树人工看）。
- 号名→目录必须走 `resolve_dir()` 模糊匹配（号名「猫笔刀」≠ 目录「猫刀笔」，否则会拆成两本）。
- **元数据：作者 `dc:creator` = 公众号博主名**（不是文章笔名 moomoocat）；**排序作者恒为「沪上陈少」**
  （`opf:file-as` + `refines meta` 两种都写，兼容 EPUB2/calibre 与 EPUB3）。
- skill `html-to-sigil-converter` 在 `/Users/jessper/Documents/Codex/2026-09-09/ze/skills/`（不在 ~/.workbuddy/skills）。

## 已交付
- `html_to_epub.py`（HTML→EPUB，含本地图回退 + 图片远程下载 + XML 良构清洗）
- `fetch_links.py`（链接批量抓取，验证页退避重试）
- `yuanchuan_crawler.py`（需要微信登录态的全量列表抓取，备选方案 B）
- 依赖 `lxml` 装在 managed venv：`/Users/jessper/.workbuddy/binaries/python/envs/default`
- 已合成：`微信文章_已下4篇.epub`（4 篇短链，原貌含图）
