#!/usr/bin/env bash
# 双击打开成书看板：没在跑就后台起服务，再开浏览器。
# 服务是脱离终端的（nohup），关掉这个窗口也照常运行。
cd "$(dirname "$0")" || exit 1
./dash_service.sh start
