#!/bin/bash
cd "/Users/gchyang/文档/synchronize/On going job/2026OMC/Projects/videoeditor"

echo "=== 修复 git 状态 ==="
rm -f .git/index .git/refs/heads/master .git/refs/heads/main .git/COMMIT_EDITMSG
find .git/logs -type f -delete 2>/dev/null

git symbolic-ref HEAD refs/heads/main

echo "=== git add ==="
git add .gitignore src/ docs/ eval/ materials/
git status --short

echo "=== git commit ==="
git commit -m "feat: add tianbaba filter preset + wire enhance into roughcut pipeline

- enhance.py: tianbaba preset (colorlevels 去灰 + saturation 1.35 + unsharp 1.2)
- roughcut.py: --enhance / --enhance-grade flags, Step 6 after render
- .gitignore: exclude reference/ *.mp4 output/ models/ *.wav"

echo ""
echo "=== 当前 remote ==="
git remote -v

echo ""
echo "✅ commit 完成。如需 push，运行："
echo "   git remote add origin <your-github-url>"
echo "   git push -u origin main"
echo ""
read -p "按回车关闭..."
