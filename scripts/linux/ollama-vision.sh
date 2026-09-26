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
# num_ctx reloads it (10-40 s on the 3090). Poindexter runs every call routed
# here at app_settings.pinned_llm_endpoint_num_ctx (the dispatcher, the warm job
# and the brain's RAM recycle all send it). A caller that sends none got
# Ollama's VRAM-derived default instead, 32768 on this 24 GB card:
# glad-labs-products/sanctuary calls every 10 min with no num_ctx, and on
# 2026-09-25 that reloaded the judge 12 times in an hour. This is the floor for
# such callers. The default below must equal that setting's declared default
# (test_ollama_vision_context_pin.py enforces it); if you retune the setting
# live, export the same value here and restart the unit.
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-16384}"
# llama-server keeps a host-RAM prompt cache of finished requests' KV state
# (llama.cpp #16391) so a later prompt sharing a prefix skips re-prefilling it.
# Ollama runs llama-server with no --cache-ram, so the upstream 8192 MiB
# default applied — plenty for text, but a vision-heavy mix rides ~46 MiB of
# untracked image data per cached frame above whatever the cap is, so it
# plateaued at 10.5-10.7 GiB (2026-09-25) and the brain's
# ollama_runner_ram_watch had to recycle the runner to get the memory back.
# LLAMA_ARG_CACHE_RAM caps it at the source instead. 3072 MiB keeps 97% of
# hits / 99% of reused prefill tokens (measured over 2026-09-24/25's 1,347
# lookups; a 2048 MiB cap keeps only 74%/61%, a 512 MiB cap 55%/36%) while
# capping the plateau at roughly cap + one frame's image overhead + base RSS
# (~4.2 GiB here) instead of drifting toward the old 10.5+ GiB. The default
# below must equal pinned_llm_endpoint_cache_ram_mib's declared default
# (test_ollama_vision_context_pin.py enforces it); if you retune the setting
# live, export the same value here and restart the unit. Raising
# ollama_runner_ram_recycle_targets's watermark to sit above that plateau (see
# settings_defaults.py) is what turns the recycle from the only containment
# into a backstop against runaway growth beyond it.
export LLAMA_ARG_CACHE_RAM="${LLAMA_ARG_CACHE_RAM:-3072}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/data/ollama/models}"
exec ollama serve
