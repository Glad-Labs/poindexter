<#
.SYNOPSIS
    Second Ollama instance pinned to GPU 1 (RTX 3090), serving on port 11435.

.DESCRIPTION
    Placement pinning for eviction-prone QA-rail models (glad-labs-stack#2051).
    The primary Ollama on :11434 sees both GPUs and evicts whatever the writer
    reload displaces, so qwen3-vl cold-loads mid-pipeline, times out, and
    qa.vision passes open. This instance sees ONLY the 3090
    (CUDA_VISIBLE_DEVICES=1) and never unloads (OLLAMA_KEEP_ALIVE=-1), so
    models routed here stay warm.

    App-side routing (shipped in PR #2074) reads the app_settings key
    plugin.llm_provider.litellm.config.model_api_base_overrides, e.g.
    {"ollama/qwen3-vl:30b": "http://host.docker.internal:11435"}.

    Binds loopback only - Docker Desktop proxies host.docker.internal to the
    host loopback, which is the same proven path the primary on :11434 uses.
    Shares the model store with the primary (OLLAMA_MODELS + tuning vars
    inherited from user env); run pulls against the primary, this instance
    only serves.

    Registered as scheduled task 'poindexter-ollama-vision-gpu1' by
    background-services.ps1.
#>

# Import the operator's user-level OLLAMA_* tuning vars (OLLAMA_MODELS,
# flash-attn, KV cache type, num_ctx). Scheduled-task processes do not
# reliably inherit HKCU environment, so read the registry explicitly -
# without this the instance falls back to the default ~\.ollama\models
# store and reports every model as not found.
$userEnv = [Environment]::GetEnvironmentVariables('User')
foreach ($key in $userEnv.Keys) {
    if ($key -like 'OLLAMA_*') { Set-Item -Path "env:$key" -Value $userEnv[$key] }
}

# Pin vars come AFTER the import so they always win.
# A bare numeric CUDA_VISIBLE_DEVICES index is unreliable on recent Ollama
# builds - observed loading onto GPU 0 despite =1 (Ollama 0.31.1). The GPU
# UUID form confines correctly (and is what the Ollama FAQ recommends), so
# resolve the UUID of nvidia-smi index 1 (the RTX 3090) at launch.
$gpuUuid = (& nvidia-smi --query-gpu=uuid --format=csv,noheader -i 1).Trim()
if (-not $gpuUuid -or $gpuUuid -notlike 'GPU-*') {
    throw "ollama-vision-gpu1: could not resolve UUID for GPU index 1 (got '$gpuUuid') - refusing to start unpinned"
}
$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"    # deterministic index mapping across driver updates
$env:CUDA_VISIBLE_DEVICES = $gpuUuid     # RTX 3090 only (CUDA backend)
# Ollama 0.31+ also ships a Vulkan backend, ON by default, that enumerates
# GPUs independently - CUDA_VISIBLE_DEVICES does not apply to it, so the
# scheduler still saw the 5090 as Vulkan0 and picked it (more free VRAM).
# Disabling Vulkan leaves exactly one visible device: the pinned 3090.
$env:OLLAMA_VULKAN = "false"
$env:OLLAMA_HOST = "127.0.0.1:11435"
$env:OLLAMA_KEEP_ALIVE = "-1"            # never unload - the whole point of this instance
$env:OLLAMA_MAX_LOADED_MODELS = "1"

& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
