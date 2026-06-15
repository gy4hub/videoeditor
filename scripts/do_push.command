#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"
git remote add origin https://github.com/gy4hub/videoeditor.git 2>/dev/null || git remote set-url origin https://github.com/gy4hub/videoeditor.git
git push -u origin main
echo "=== push 完成 ==="
read -p "按回车关闭..."
