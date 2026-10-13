#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内置来源适配器。

加一种新来源：在这里 import 它即可（其余两步见 core/pipeline.py 顶部说明）。
import 失败会被吞掉并记录审计——某个 adapter 坏了不能让整个系统起不来。
"""

from __future__ import annotations

from .. import audit

# 顺序即优先级：先微信直取（特征更具体），再 Notion（泛一些）。
# 认错来源会导致整篇产出格式不对，所以把判定更严的放前面。
_ADAPTERS = ("wechat", "notion_wechat")

for _m in _ADAPTERS:
    try:
        __import__(f"{__name__}.{_m}")
    except Exception as e:      # 单个 adapter 坏了不影响其他
        audit.log("adapter.load.fail", level="error", adapter=_m,
                  error=f"{type(e).__name__}: {e}")
