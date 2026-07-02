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

$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"    # deterministic index mapping across driver updates
$env:CUDA_VISIBLE_DEVICES = "1"          # RTX 3090 only
$env:OLLAMA_HOST = "127.0.0.1:11435"
$env:OLLAMA_KEEP_ALIVE = "-1"            # never unload - the whole point of this instance
$env:OLLAMA_MAX_LOADED_MODELS = "1"

& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
