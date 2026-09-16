#!/usr/bin/env bash
# 双击运行：收件箱 → 归档 → 成书 → 体检 → 回归比对 → 打开结果
cd "$(dirname "$0")" || exit 1

echo "======== 1/3 收件箱 → 归档 → 成书 ========"
./epub.sh inbox

echo
echo "======== 2/3 交付前体检 ========"
./epub.sh check

echo
echo "======== 3/3 回归比对 ========"
./epub.sh test

echo
echo "明细看 _处理报告.md，家底看 _台账.md"
open ..
read -rsp $'按回车关闭窗口…\n'
