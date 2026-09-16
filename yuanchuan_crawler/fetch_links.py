#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
指定链接批量抓取微信原文 HTML -> raw_html/ （供 html_to_epub.py 合成）

应对微信「环境异常」频率风控：
  - 移动端 UA（微信内置浏览器）
  - 命中验证页（无 js_content / 体积过小）自动退避重试
  - 每篇之间留间隔，避免触发风控

用法：
  python3 fetch_links.py link1 link2 ...
  python3 fetch_links.py            # 读取本文件 LINKS 列表
"""
import os, re, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw_html")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.38")
REF = "https://mp.weixin.qq.com/"

# 默认链接列表（命令行参数可覆盖）
LINKS = [
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498355&idx=1&sn=107d23382491545a491d803a33c2c1b6&chksm=97dbb64c3697aa520c1c7b75386ed3ae27c4cb065d352457467cd37902ebcc2d0d9c02751566#rd",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498337&idx=1&sn=625f66280887d84340ab1b6f6fbe437a&chksm=9701d5463b2a48cbbea521c6abaee201e77cca85712a6033c010184625c8448d3d39e567ff40#rd",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498331&idx=1&sn=a64f609611d97f01103cd94b8705e471&chksm=9773a141602530728337f5698f6624912cab79250bb50e3abc61723f5b612922f74bae746aec#rd",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498322&idx=1&sn=b9f56b9d51a25707cedf7a09e3e50c7f&chksm=97eaeb61d8864566e3c297237fd380505ef4554e7162bca005c0e3dbb2935fa12ebc3d27f901#rd",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498318&idx=1&sn=fa4eb76cdd2b2af5784bc826b0b56e9b&chksm=97982259d48b99bbb3360fe470a6ec726dfe501f99f23df6b588f1e154d97bef5d11e5c1804a#rd",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498308&idx=1&sn=d5bfd2ff7261863f85dfb156991d0302&chksm=9792f70d4f5c423f2c3924e4ed5c08d901381f9cea470118c95f883a1c4b25098f6698ecff74#rd",
    "https://mp.weixin.qq.com/s/9SpQ9QvJJ_e21z-ZxqioKw",
    "https://mp.weixin.qq.com/s/wLapJOwEhuGWAl6QYEggDQ",
    "https://mp.weixin.qq.com/s/FIVNWcTvrOOKafpogYdOyw",
    "https://mp.weixin.qq.com/s/B6a5lB6Hax9bHjdDn0jWWQ",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498223&idx=1&sn=09e5fdef93c47364e4bf69ec982d57e9&chksm=97e52c7c859c1ebb38dcdb31130d0be68a84d7e558daf2894981d4d188c092b5fe894d174e74#rd",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498190&idx=1&sn=d39e4d284d2d039e77fc7d20bc8b471a&chksm=97a277aec18360089fa479771d54379a6305e0ef6aa01cadc00bd36c73dbed4399dc9bb31eed#rd",
    "https://mp.weixin.qq.com/s?__biz=MzE5ODk2NjUwOA==&mid=2247498180&idx=1&sn=f4b84dd210fb8a608f18e71c0024c3fb&chksm=978bddf48c47ad900e67b9d5476d5bb11dec343904fbae752f2f2dc5d4d909fb8c0a9e87b0cf#rd",
]


def log(*a):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), " ".join(str(x) for x in a)), flush=True)


def is_blocked(txt):
    if 'id="js_content"' not in txt:
        return True
    if "环境异常" in txt or "验证" in txt[:500]:
        return True
    return False


def fetch_one(url, max_retry=4):
    delay = 5
    for attempt in range(1, max_retry + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
            data = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            log("  HTTP %s，%ds 后重试" % (e.code, delay))
            time.sleep(delay); delay = min(delay * 2, 30); continue
        except Exception as e:
            log("  异常 %s，%ds 后重试" % (e, delay))
            time.sleep(delay); delay = min(delay * 2, 30); continue
        if is_blocked(data):
            log("  验证页(第%d次)，退避 %ds" % (attempt, delay))
            time.sleep(delay); delay = min(delay * 2, 30); continue
        return data
    return None


def main():
    urls = sys.argv[1:] or LINKS
    os.makedirs(RAW, exist_ok=True)
    ok, fail = [], []
    for i, url in enumerate(urls, 1):
        log("(%d/%d) 抓取 %s" % (i, len(urls), url[:60]))
        txt = fetch_one(url)
        if not txt:
            fail.append(url)
            log("  ✗ 放弃（风控）")
            continue
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)', txt)
        title = (m.group(1).strip() if m else "link%d" % i)[:60]
        fn = "art_%02d_%s.html" % (i, re.sub(r'[^\w一-鿿-]', '', title)[:20] or i)
        open(os.path.join(RAW, fn), "w").write(txt)
        ok.append((fn, title))
        log("  ✓ %s  (%dKB)" % (title, len(txt) // 1024))
        time.sleep(4)  # 篇间间隔，降低风控概率
    log("完成：成功 %d 篇，失败 %d 篇" % (len(ok), len(fail)))
    for fn, t in ok:
        log("  已存", fn, t)
    if fail:
        log("失败链接（需本机另存 HTML 补）：")
        for u in fail:
            log("  ", u)


if __name__ == "__main__":
    main()
