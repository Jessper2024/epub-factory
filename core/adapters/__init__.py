#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内置来源适配器。

加一种新来源：在这里 import 它即可（其余两步见 core/pipeline.py 顶部说明）。
import 失败会被吞掉并记录审计——某个 adapter 坏了不能让整个系统起不来。
"""

from __future__ import annotations

from .. import audit

_ADAPTERS = ("wechat",)

for _m in _ADAPTERS:
    try:
        __import__(f"{__name__}.{_m}")
    except Exception as e:      # 单个 adapter 坏了不影响其他
        audit.log("adapter.load.fail", level="error", adapter=_m,
                  error=f"{type(e).__name__}: {e}")
