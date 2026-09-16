#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可扩展处理管线：新增一种"来源类型"只需要加一个 adapter 文件。

为什么要有这一层
    改造前，处理逻辑直接长在 build_epub.py / sigi_convert.py 里，微信的规则、
    路径、转换入口散在各处。将来要支持第二种来源（网页正文、RSS、PDF 转文字…），
    只能在主流程里继续加 if-else，越加越脆。
    现在把"一种来源怎么被认出来、怎么抽正文、怎么变成 XHTML"收敛成一个协议，
    主流程只跟协议打交道。

协议（SourceAdapter）
    name        str                     来源类型名，如 "wechat"
    identify(path) -> bool             这个文件是不是我这种来源
    extract(path) -> RawDoc             抽出标题/日期/作者/号名/正文
    render(doc, out_path) -> Path       渲染成 Sigil 可用的 XHTML

依赖方向（重要）
    core 不 import 业务脚本（sigi_convert / build_epub），否则会循环依赖、
    且 import 业务模块会触发它的模块级副作用。
    所以 adapter 里对业务脚本的 import 一律**写在函数内部**（延迟导入）。

怎么加一种新来源（三步，不动主流程）
    1. 在 core/adapters/ 下新建 mykind.py，实现上面四个方法；
    2. 文件末尾 `register(MyKindAdapter())`；
    3. 在 core/adapters/__init__.py 里 import 它。
    之后 `pipeline.pick(path)` 会自动认出这种来源，`pipeline.run()` 就能转。

用法
    from core import pipeline
    ad = pipeline.pick(path)              # 自动挑 adapter
    doc = pipeline.extract(path)
    out = pipeline.run(path, out_dir)
    python3 -m core.pipeline <文件>        # 命令行试转
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import audit


@dataclass
class RawDoc:
    """一种来源被抽出后的中间表示。渲染前的统一形态。

    body_html 是干净正文片段；其余是元数据。缺失字段留空字符串，不要填猜测值
    （历史教训：猜出来的作者/号名会建出错误的号目录，把书拆成两本）。
    """
    title: str = ""
    date: str = ""            # YYYY年M月D日
    author: str = ""
    account: str = ""         # 公众号 / 来源名
    body_html: str = ""
    source_path: str = ""
    kind: str = ""
    meta: dict = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def identify(self, path: Path) -> bool: ...
    def extract(self, path: Path) -> RawDoc: ...
    def render(self, doc: RawDoc, out_path: Path) -> Path: ...


_REGISTRY: dict[str, SourceAdapter] = {}


def register(adapter: SourceAdapter) -> None:
    """注册一种来源适配器。同名覆盖（后注册的优先——方便测试时替换）。"""
    _REGISTRY[adapter.name] = adapter


def registry() -> dict[str, SourceAdapter]:
    return dict(_REGISTRY)


def load_builtins() -> None:
    """加载内置 adapter。单独一个函数，避免 import core.pipeline 就顺带拉起
    所有 adapter 的模块级开销（它们各自延迟导入业务脚本，但仍尽量按需）。"""
    try:
        from . import adapters  # noqa: F401
    except Exception as e:      # adapter 坏了不能让整个 core 起不来
        audit.log("pipeline.load_builtins.fail", level="error", error=f"{type(e).__name__}: {e}")


def pick(path: Path) -> SourceAdapter | None:
    """按注册顺序挑出能处理这个文件的 adapter。都认不出返回 None（不要瞎猜）。"""
    p = Path(path)
    if not _REGISTRY:
        load_builtins()
    for ad in _REGISTRY.values():
        try:
            if ad.identify(p):
                return ad
        except Exception:
            continue
    return None


def extract(path: Path) -> RawDoc:
    """用合适的 adapter 抽内容。认不出来抛 ValueError（不是静默返回空文档）。"""
    ad = pick(path)
    if ad is None:
        raise ValueError(f"没有 adapter 能处理：{path}")
    return ad.extract(Path(path))


def run(path: Path, out_dir: Path | None = None) -> Path:
    """认类型 → 抽内容 → 渲染 XHTML。一条龙。

    输出文件名由 adapter 决定（沿用现有命名约定：日期_号_作者_标题-Sigil.xhtml）。
    """
    p = Path(path)
    ad = pick(p)
    if ad is None:
        raise ValueError(f"没有 adapter 能处理：{p}")
    out_dir = Path(out_dir) if out_dir else p.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    with audit.task("pipeline.run", kind=ad.name, file=p.name) as t:
        doc = ad.extract(p)
        out = ad.render(doc, out_dir)
        t.stats(title=doc.title[:40], out=str(out))
    return out


if __name__ == "__main__":
    import sys
    # `python -m core.pipeline` 会把本文件当 __main__ 执行。若不把自己也登记成
    # "core.pipeline"，adapter 里的 `from ..pipeline import register` 会另起一份
    # 模块实例，注册就落到那份上——本份的 registry 永远是空的（踩过，现象是
    # "已注册来源" 空、识别不出任何文件）。
    sys.modules.setdefault("core.pipeline", sys.modules["__main__"])

    if len(sys.argv) < 2:
        print("用法: python3 -m core.pipeline <文件> [输出目录]")
        print("已注册来源:", ", ".join(registry()) or "（空，先跑 load_builtins）")
        raise SystemExit(1)
    load_builtins()
    print("已注册来源:", ", ".join(registry()))
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    ad = pick(src)
    print("识别为:", ad.name if ad else "（认不出）")
    if ad:
        doc = ad.extract(src)
        print(f"  标题  {doc.title}\n  日期  {doc.date}\n  作者  {doc.author}\n  号名  {doc.account}")
        if out_dir:
            print("  输出 ", run(src, out_dir))
