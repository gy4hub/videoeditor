#!/bin/bash
# Run this once in Terminal from the videoeditor folder to init git and push.
# Usage:  bash git_init_and_push.sh <your-github-remote-url>
# e.g.:   bash git_init_and_push.sh git@github.com:gchyang/videoeditor.git

set -e
REMOTE="${1:-}"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== videoeditor git setup ==="
echo "Working directory: $DIR"

# Remove stale lock left by Cowork session
if [ -f ".git/index.lock" ]; then
  echo "[1/5] Removing stale .git/index.lock ..."
  rm -f .git/index.lock
fi

# Init (idempotent if already exists)
if [ ! -f ".git/HEAD" ]; then
  echo "[2/5] git init ..."
  git init -b main
else
  echo "[2/5] .git already exists, skipping init"
  git checkout -b main 2>/dev/null || git checkout main 2>/dev/null || true
fi

git config user.email "gchyang0808@gmail.com"
git config user.name "Chen"

echo "[3/5] Staging files ..."
git add .gitignore src/ docs/ eval/ materials/ 2>/dev/null || true

echo "[4/5] Committing ..."
git commit -m "feat: add tianbaba filter preset + wire enhance into roughcut pipeline

- enhance.py: add 'tianbaba' preset (去灰/去雾/高清增强)
  * colorlevels black-point lift (rimin/gimin=0.04) → 去灰核心
  * eq saturation=1.35 → 还原绿植/天空通透感
  * unsharp luma_amount=1.2 → 高清增强细节
  * hqdn3d=2:2:4:4 → 轻降噪保留纹理
  * 对标 SRN901抗衰突破.mp4（剪映高清增强+去灰+去雾）
  * 设为 DEFAULT_GRADE

- roughcut.py: add --enhance / --enhance-grade flags
  * Step 6 渲染后自动滤镜 → *_hd.mp4
  * 非致命：滤镜失败仅警告，不中止管线

- .gitignore: 排除 reference/ *.mp4 output/ models/ *.wav" || echo "(nothing to commit, already up to date)"

if [ -n "$REMOTE" ]; then
  echo "[5/5] Adding remote and pushing ..."
  git remote add origin "$REMOTE" 2>/dev/null || git remote set-url origin "$REMOTE"
  git push -u origin main
  echo "=== Done! Pushed to $REMOTE ==="
else
  echo "[5/5] No remote URL provided."
  echo "      To push, run:"
  echo "      git remote add origin <your-repo-url>"
  echo "      git push -u origin main"
  echo "=== Commit ready. ==="
fi
