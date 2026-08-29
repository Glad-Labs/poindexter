#!/usr/bin/env bash
# ollama-primary.sh — pin the primary Ollama instance to the FIRST GPU
# (nvidia-smi index 0) by UUID and serve it on :11434.
#
# Mirror of ollama-vision.sh, which pins index 1. Same two traps apply: a bare
# numeric CUDA index is unreliable across boots, and Ollama's Vulkan backend
# ignores CUDA_VISIBLE_DEVICES entirely — so resolve the UUID and disable
# Vulkan, or the pin is silently a no-op.
#
# WHY PIN AT ALL (poindexter#3457 Phase 3)
# =======================================
#
# The GPU advisory lock serialises on the DEVICE SETS a caller can contend for.
# That only buys anything when the sets are genuinely disjoint, and disjoint
# has to mean ENFORCED, not merely declared:
#
#   qa_judge     {1}    enforced by ollama-vision.sh (refuses to start unpinned)
#   render       {0}    image-gen / wan / comfyui
#   llm_primary  {0,1}  <- unenforced until this script existed
#
# While primary could land on either card, its set overlapped both others, so
# every scope still serialised and the judges kept queueing behind renders (238
# skipped QA rails in 7 days). Declaring `llm_primary: [0]` WITHOUT this pin
# would be strictly worse than doing nothing: the lock would stop serialising
# judge-vs-primary while the hardware still let them collide on GPU 1 — a CUDA
# OOM waiting to happen. The pin is what makes the declaration true.
#
# Cost: primary can no longer spill onto GPU 1. In practice that capacity is
# already gone — the judge holds ~21 of GPU 1's 23.6 GiB with KEEP_ALIVE=-1 —
# and GPU 0 is the larger card (32.6 GiB).
#
# If you UNPIN this, also widen `gpu_lock_scopes.llm_primary` back to [0,1] and
# restore `ollama_gpu_indexes=0,1`, or the lock will believe a disjointness
# that no longer exists.
set -euo pipefail
uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader -i 0 | tr -d '[:space:]')"
case "$uuid" in
  GPU-*) ;;
  *) echo "ollama-primary: bad UUID '$uuid' for GPU 0 — refusing to start unpinned" >&2; exit 1 ;;
esac
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES="$uuid"
export OLLAMA_VULKAN=false
export OLLAMA_HOST="${OLLAMA_HOST:-0.0.0.0:11434}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-8192}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/data/ollama/models}"
exec ollama serve
