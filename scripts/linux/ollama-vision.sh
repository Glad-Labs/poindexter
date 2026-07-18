#!/usr/bin/env bash
# ollama-vision.sh — pin the vision Ollama instance to the SECOND GPU
# (nvidia-smi index 1) by UUID and serve it on :11435, never unloading.
#
# A bare numeric CUDA index is unreliable, and Ollama's Vulkan backend ignores
# CUDA_VISIBLE_DEVICES entirely, so resolve the UUID and disable Vulkan. Faithful
# port of the Windows scripts/ollama-vision-gpu1.ps1.
set -euo pipefail
uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1 | tr -d '[:space:]')"
case "$uuid" in
  GPU-*) ;;
  *) echo "ollama-vision: bad UUID '$uuid' for GPU 1 — refusing to start unpinned" >&2; exit 1 ;;
esac
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES="$uuid"
export OLLAMA_VULKAN=false
export OLLAMA_HOST=0.0.0.0:11435          # 0.0.0.0 so containers reach it via host-gateway
export OLLAMA_KEEP_ALIVE=-1               # never unload — the whole point of this instance
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_MODELS="${OLLAMA_MODELS:-/data/ollama/models}"
exec ollama serve
