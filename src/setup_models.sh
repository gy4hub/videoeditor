#!/usr/bin/env bash
# setup_models.sh — 预下载 faster-whisper 模型到本地 HF cache
#
# 绕过 hf_transfer 兼容问题（aarch64 沙箱 hf_transfer rust 加速器静默失败），
# 直接用 curl 从 HuggingFace raw 端点下载，写入标准 HF cache 目录。
# WhisperModel(local_path) 可直接加载。
#
# 用法:
#   bash src/setup_models.sh            # 默认下载 base 模型
#   bash src/setup_models.sh base       # 同上
#   bash src/setup_models.sh small      # 下载 small 模型
#   bash src/setup_models.sh tiny       # 下载 tiny 模型
#
# 环境变量:
#   HF_CACHE_DIR  — 可覆盖 cache 根目录（默认 ~/.cache/huggingface/hub）

set -euo pipefail

# ─── 配置 ─────────────────────────────────────────────────────────────────────
MODEL_SIZE="${1:-base}"
HF_CACHE_DIR="${HF_CACHE_DIR:-${HOME}/.cache/huggingface/hub}"
HF_BASE_URL="https://huggingface.co/Systran/faster-whisper-${MODEL_SIZE}/resolve/main"

# faster-whisper 模型所需文件列表（preprocessor_config.json 不存在于该仓库，跳过）
MODEL_FILES=(
    "config.json"
    "tokenizer.json"
    "vocabulary.txt"
    "model.bin"
)
# ─────────────────────────────────────────────────────────────────────────────

DEST_DIR="${HF_CACHE_DIR}/models--Systran--faster-whisper-${MODEL_SIZE}/snapshots/main"

echo "[setup_models] Model: faster-whisper-${MODEL_SIZE}"
echo "[setup_models] Cache dir: ${DEST_DIR}"

mkdir -p "${DEST_DIR}"

ALL_OK=true
for f in "${MODEL_FILES[@]}"; do
    TARGET="${DEST_DIR}/${f}"
    if [[ -f "${TARGET}" && -s "${TARGET}" ]]; then
        echo "[setup_models] SKIP (exists) ${f}"
        continue
    fi
    URL="${HF_BASE_URL}/${f}"
    echo "[setup_models] Downloading ${f} ..."
    # -L: follow redirects; -f: fail on HTTP error; --retry 3; -# progress bar
    if curl -L -f --retry 3 --retry-delay 2 -# -o "${TARGET}" "${URL}"; then
        SIZE=$(wc -c < "${TARGET}")
        echo "[setup_models] OK  ${f}  (${SIZE} bytes)"
    else
        echo "[setup_models] FAIL ${f} — 下载失败，跳过" >&2
        rm -f "${TARGET}"
        ALL_OK=false
    fi
done

# 打印最终结果
echo ""
echo "[setup_models] 文件清单:"
for f in "${MODEL_FILES[@]}"; do
    TARGET="${DEST_DIR}/${f}"
    if [[ -f "${TARGET}" && -s "${TARGET}" ]]; then
        SIZE=$(wc -c < "${TARGET}")
        printf "  %-30s %s bytes\n" "${f}" "${SIZE}"
    else
        printf "  %-30s MISSING\n" "${f}"
    fi
done

if $ALL_OK; then
    echo ""
    echo "[setup_models] 完成！模型路径: ${DEST_DIR}"
    echo "  使用方法: WhisperModel('${DEST_DIR}')"
    exit 0
else
    echo ""
    echo "[setup_models] 部分文件下载失败，请检查网络或尝试其他模型（tiny/small）" >&2
    exit 1
fi
