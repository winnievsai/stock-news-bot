#!/bin/bash
# 把本機修改過的程式碼同步到 GitHub。
# 用法：在終端機執行 ./update.sh
cd "$(dirname "$0")"

git add -A
git commit -m "Update $(date '+%Y-%m-%d %H:%M')" || echo "沒有變動可以 commit"
git push
