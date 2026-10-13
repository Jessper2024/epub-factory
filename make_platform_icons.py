#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 5 个平台图标（白底 + 平台色边框 + 平台色图形）。

陈少 2026-10-13 定：所有平台图标统一**白底彩框**，贴 PDF 标题块左侧。
    微信公众号  绿  #07C160
    知乎        蓝  #0084FF
    Twitter     黑  #000000（原来黑底，改成白底黑框）
    微博        橙红 #E6162D
    小红书      红  #FF2442（原来红底，改成白底红框）

不复用陈少发的截图（那些带背景、分辨率不一），直接矢量式重绘 —— 干净、
任意缩放不糊、颜色统一可控。输出 PNG（300×300，2x 够打印）。

用法：python3 make_platform_icons.py [输出目录]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DEFAULT = Path(__file__).resolve().parent / "assets" / "platform-icons"

# (平台名, 主色, key)
PLATFORMS = (
    ("微信公众号", "#07C160", "wechat"),
    ("知乎", "#0084FF", "zhihu"),
    ("Twitter", "#000000", "twitter"),
    ("微博", "#E6162D", "weibo"),
    ("小红书", "#FF2442", "xiaohongshu"),
)

SIZE = 300          # 画布
PAD = 30            # 外留白
BOX = SIZE - PAD * 2
R = 62              # 圆角半径
BORDER = 8          # 边框粗细

FONT_CANDIDATES = (
    "/Users/jessper/Library/Fonts/方正屏显臻宋.TTF",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)


def _font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_wechat(d: ImageDraw.ImageDraw, color: str, cx: int, cy: int):
    """对话气泡 + 两个点（微信标志的简化）"""
    w, h = 150, 122
    d.rounded_rectangle([cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2 - 16],
                        radius=40, fill=color)
    # 气泡小尾巴
    d.polygon([(cx - 34, cy + h // 2 - 18), (cx - 4, cy + h // 2 - 18),
               (cx - 30, cy + h // 2 + 10)], fill=color)
    # 两个白点
    for dx in (-32, 32):
        d.ellipse([cx + dx - 11, cy - 24, cx + dx + 11, cy - 2], fill="#FFFFFF")


def _draw_zhihu(d: ImageDraw.ImageDraw, color: str, cx: int, cy: int):
    """白底蓝框 + 「知」字"""
    f = _font(180)
    txt = "知"
    box = d.textbbox((0, 0), txt, font=f)
    d.text((cx - (box[2] - box[0]) / 2 - box[0], cy - (box[3] - box[1]) / 2 - box[1]),
           txt, font=f, fill=color)


def _draw_twitter(d: ImageDraw.ImageDraw, color: str, cx: int, cy: int):
    """X 字母（两条粗斜线）"""
    s = 82
    w = 30
    d.line([(cx - s, cy - s), (cx + s, cy + s)], fill=color, width=w)
    d.line([(cx + s, cy - s), (cx - s, cy + s)], fill=color, width=w)


def _draw_weibo(d: ImageDraw.ImageDraw, color: str, cx: int, cy: int):
    """微博眼睛 + 信号弧"""
    # 眼睛轮廓（横椭圆）
    d.ellipse([cx - 96, cy - 44, cx + 52, cy + 52], fill=color)
    # 眼白
    d.ellipse([cx - 72, cy - 26, cx + 30, cy + 34], fill="#FFFFFF")
    # 瞳孔
    d.ellipse([cx - 40, cy - 16, cx + 4, cy + 24], fill=color)
    # 信号弧（右上）
    for i, rr in enumerate((38, 60, 82)):
        d.arc([cx + 30 - rr, cy - 30 - rr, cx + 30 + rr, cy - 30 + rr],
              start=-78, end=-14, fill=color, width=11)


def _draw_xiaohongshu(d: ImageDraw.ImageDraw, color: str, cx: int, cy: int):
    """「小红书」三个字横排（竖排在方框里挤，横排更好认）。"""
    f = _font(74)
    txt = "小红书"
    gap = 4
    widths = []
    for ch in txt:
        b = d.textbbox((0, 0), ch, font=f)
        widths.append(b[2] - b[0])
    total = sum(widths) + gap * (len(txt) - 1)
    x = cx - total / 2
    for i, ch in enumerate(txt):
        b = d.textbbox((0, 0), ch, font=f)
        d.text((x - b[0], cy - (b[3] - b[1]) / 2 - b[1]), ch, font=f, fill=color)
        x += widths[i] + gap


DRAWERS = {
    "wechat": _draw_wechat,
    "zhihu": _draw_zhihu,
    "twitter": _draw_twitter,
    "weibo": _draw_weibo,
    "xiaohongshu": _draw_xiaohongshu,
}


def build(outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    made = []
    for name, color, key in PLATFORMS:
        img = Image.new("RGB", (SIZE, SIZE), "#FFFFFF")
        d = ImageDraw.Draw(img)
        # 白底 + 平台色圆角边框
        d.rounded_rectangle([PAD, PAD, PAD + BOX, PAD + BOX], radius=R,
                            fill="#FFFFFF", outline=color, width=BORDER)
        DRAWERS[key](d, color, SIZE // 2, SIZE // 2)
        out = outdir / ("%s.png" % key)
        img.save(out, "PNG")
        made.append(out)
        print("  ✓ %s  %s" % (out.name, color))
    return made


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    print("生成平台图标 → %s" % outdir)
    build(outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
