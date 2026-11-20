#!/usr/bin/env bash
# Installer for Alis-Companion local ML Auto Thinking Effort Router
# Uses Horizon-Labs/multilingual-prompt-task-complexity-small (mmBERT-small INT8 ONNX, Apache-2.0)
set -euo pipefail

XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
ROUTER_ROOT="${ALIS_THINKING_ROUTER_DIR:-$XDG_DATA_HOME/alis-companion/thinking-router}"
VENV_DIR="$ROUTER_ROOT/venv"
VENV_PY="$VENV_DIR/bin/python"
MODEL_DIR="$ROUTER_ROOT/model"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HF_BASE="https://huggingface.co/Horizon-Labs/multilingual-prompt-task-complexity-small/resolve/main"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' is required to install the local Thinking Router environment (sudo pacman -S uv)." >&2
    exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: 'curl' is required to download mmBERT ONNX artifacts." >&2
    exit 1
fi

mkdir -p "$ROUTER_ROOT" "$MODEL_DIR"

echo "[thinking-router] Creating isolated CPU Python environment at $VENV_DIR..."
# Prefer Python 3.12 when available so prebuilt onnxruntime/tokenizers wheels install reliably.
if ! uv venv -q --clear --python 3.12 "$VENV_DIR" 2>/dev/null; then
    uv venv -q --clear "$VENV_DIR"
fi

echo "[thinking-router] Installing onnxruntime, tokenizers, numpy..."
uv pip install -q --python "$VENV_PY" onnxruntime tokenizers numpy

download_artifact() {
    local remote_path="$1"
    local dest_path="$2"
    local tmp_path="${dest_path}.tmp.$$"
    if [ -s "$dest_path" ]; then
        return 0
    fi
    echo "[thinking-router] Downloading $remote_path..."
    curl -fL --retry 3 --retry-delay 2 --progress-bar "$HF_BASE/$remote_path" -o "$tmp_path"
    if [ ! -s "$tmp_path" ]; then
        rm -f "$tmp_path"
        echo "ERROR: Downloaded empty artifact for $remote_path" >&2
        exit 1
    fi
    mv -f "$tmp_path" "$dest_path"
}

download_artifact "config.json" "$MODEL_DIR/config.json"
download_artifact "tokenizer.json" "$MODEL_DIR/tokenizer.json"
download_artifact "onnx/model_quantized.onnx" "$MODEL_DIR/model_quantized.onnx"

echo "[thinking-router] Verifying ONNX forward pass..."
SMOKE_OUT="$("$VENV_PY" "$SCRIPT_DIR/thinking_router.py" --classify "Prove that sqrt(2) is irrational using proof by contradiction")"
echo "[thinking-router] Smoke check: $SMOKE_OUT"
if ! printf '%s' "$SMOKE_OUT" | grep -q '"source": "ml_onnx"'; then
    echo "ERROR: Smoke check did not run on local ml_onnx backend" >&2
    exit 1
fi

echo "THINKING_ROUTER_INSTALL_OK"
