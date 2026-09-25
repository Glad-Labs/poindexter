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
# The instance holds ONE model at ONE context size, and asking for a different
# num_ctx reloads it (10-40 s on the 3090). Every Poindexter caller sends 16384
# (qa_deepeval_judge_num_ctx, qa_ragas_judge_num_ctx, ollama_num_ctx), but a
# caller that sends none got Ollama's VRAM-derived default, 32768 on this
# 24 GB card: glad-labs-products/sanctuary calls every 10 min with no num_ctx,
# and on 2026-09-25 that reloaded the judge 12 times in an hour. Default the
# instance to the fleet value so a caller that omits num_ctx doesn't evict it.
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-16384}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/data/ollama/models}"
exec ollama serve
