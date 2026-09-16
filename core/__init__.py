#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPUB 工厂 · 架构基础层（core）

这一层是"横切关注点"：配置、审计、安全护栏、健康检查、可扩展管线。
设计原则（务必遵守）：

1. **core 绝不反向依赖业务脚本**。core 不能 import build_epub / promo / report，
   否则会循环依赖、且 import 业务脚本会触发它的模块级副作用（如 _load_allowed 读白名单）。
   业务脚本可以 import core，方向永远单向：业务 → core。
2. **改 core 不改行为**。core 只提供能力，不改写主流程。任何业务行为的变更都必须
   能被 `./epub.sh test` 的回归基线捕捉到。
3. **默认值必须与历史一致**。config 里的每个默认值都等于改造前代码里的硬编码值，
   否则就是"改配置改坏了书"。

子模块：
    config    集中配置（单一真相源）
    audit     结构化审计日志（JSONL，可追溯）
    safety    原子写 + 不可再生目录护栏
    health    preflight 自检
    pipeline  可扩展的处理管线（SourceAdapter 协议）
"""

from __future__ import annotations

__all__ = ["config", "audit", "safety", "health", "pipeline"]

__version__ = "1.0.0"
