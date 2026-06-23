# Single-GPU VRAM tuning (desktop-stability runbook)

When the worker shares **one** GPU with the Windows desktop (the reference box
is an RTX 5090, 32GB), VRAM pressure can freeze the whole desktop — keyboard and
mouse included. This runbook removes that failure mode and frees headroom for
longer context. It is the host-side half of the
[single-GPU VRAM budget design](../superpowers/specs/2026-06-22-single-gpu-vram-budget-stability-design.md);
the in-app changes (reranker→CPU, the budget calculator, per-phase context) ship
separately.

These are **host** actions. Ollama runs on the host
(`OLLAMA_BASE_URL=http://host.docker.internal:11434`), not inside WSL2/Docker, so
the NVIDIA driver setting and the Ollama environment variables below are set on
Windows, not in `docker-compose`.

## Why the desktop freezes (mechanism)

On Windows, when a CUDA allocation would exceed dedicated VRAM, the NVIDIA driver
does **not** hard-fail. Its _System Memory Fallback_ policy silently pages VRAM
into system RAM over PCIe ("Shared GPU Memory"). During that spill the GPU
saturates the bus moving memory, and the WDDM desktop compositor — which shares
the same GPU — starves. The result is the "memory pegging freezes my input"
symptom. The fix is to forbid the silent spill and size workloads to fit.

### Confirming it on your box

`nvidia-smi` shows dedicated VRAM but **not** the shared (spill) pool. Use the
Windows perf counter instead:

```powershell
# stream the spill pool; watch it under load
Get-Counter '\GPU Adapter Memory(*)\Shared Usage' -Continuous -SampleInterval 2

# one-shot snapshot of both pools (GB, nonzero only)
Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage' |
  Select-Object -ExpandProperty CounterSamples |
  Where-Object { $_.CookedValue -gt 0 } |
  Sort-Object CookedValue -Descending |
  Select-Object @{n='Counter';e={($_.Path -split '\\')[-2..-1] -join '\'}},
                @{n='GB';e={[math]::Round($_.CookedValue/1GB,2)}}
```

A small nonzero `Shared Usage` (well under 1GB) at idle is normal WDDM baseline.
The spill signature is `Shared Usage` climbing **multiple GB** as `Dedicated
Usage` approaches the card's limit — that is the moment the desktop stutters.

## 1. Disable NVIDIA System Memory Fallback

Make over-allocation return a clean CUDA OOM instead of freezing the compositor.

1. Open **NVIDIA Control Panel** -> **Manage 3D Settings**.
2. Find **CUDA - Sysmem Fallback Policy** (added in driver branch R535; if the
   label differs on your driver, look for "Sysmem Fallback" / "System Memory
   Fallback").
3. Set it to **Prefer No Sysmem Fallback** — globally, or per-program for the
   Ollama server / the `python.exe` that runs the worker if you want the policy
   scoped.
4. Apply.

This is a GUI/driver setting; it is intentionally **not** scripted here (poking
the driver's profile registry blind is fragile). It cooperates with the in-app
pre-load VRAM guard: the guard keeps the pipeline under budget, and this setting
is the hard backstop if anything slips through.

## 2. Enable Ollama flash-attention + q8_0 KV cache

Flash-attention plus an 8-bit KV cache roughly halves KV-cache VRAM (near
lossless), which is what lets context grow without tipping into spill. Both are
**global** Ollama server settings — there is no per-model override.

```powershell
# user scope (the tray-app Ollama inherits this). Use `setx VAR val /M` for a
# machine/service install.
setx OLLAMA_FLASH_ATTENTION 1
setx OLLAMA_KV_CACHE_TYPE q8_0
```

`OLLAMA_KV_CACHE_TYPE` only takes effect when `OLLAMA_FLASH_ATTENTION=1`.

**Restart the Ollama server to apply** — `setx` only affects processes started
_after_ it runs. Quit Ollama from the system tray and relaunch it (or reboot).
Pick a moment when no pipeline task is mid-generation; the worker will reconnect
and Prefect reclaims any interrupted task.

If a model ever regresses under q8_0 KV (e.g. the vision model), revert globally
by removing `OLLAMA_KV_CACHE_TYPE` and restarting — a per-model split would need
a second Ollama instance on another port.

## 3. Verify

```powershell
# confirm the server sees the vars
[Environment]::GetEnvironmentVariable('OLLAMA_FLASH_ATTENTION','User')
[Environment]::GetEnvironmentVariable('OLLAMA_KV_CACHE_TYPE','User')

# load a model and re-run the shared-usage counter under load; it should now
# stay flat (no spill) instead of climbing, and the same context fits in less
# dedicated VRAM than before.
ollama run gemma-4-31B-it-qat:latest "ok" ; ollama ps
```

Re-run the streaming counter from "Confirming it on your box" while a content
task runs end-to-end. Success = `Shared Usage` stays at its idle baseline and the
desktop stays responsive.

## 4. Watch headroom in Grafana

The **Hardware & Power** dashboard (`/d/hardware-power`) carries a **VRAM
headroom (dedicated)** panel in the _GPU — live (Prometheus)_ row. It plots:

```
( gpu_vram_total_gb - gpu_desktop_reserve_gb ) - VRAM used
= ((32 - 3) * 1GiB) - (nvidia_gpu_memory_used_mib * 1MiB)
```

The 32 / 3 literals mirror the `gpu_vram_total_gb` / `gpu_desktop_reserve_gb`
app_settings the in-app dispatcher clamp reads, so the panel shows the same
budget the guard enforces. The series is blue with comfortable headroom and
turns amber as it approaches the **0 line** (the threshold line is the spill
boundary); crossing 0 means the projected footprint has eaten the desktop
reserve. (Blue/amber, not red/green — the operator is red-green colorblind.)

If headroom trends toward 0 during normal runs, either lower a context-hungry
phase's `<phase>_num_ctx`, raise `gpu_desktop_reserve_gb`, or confirm
`OLLAMA_KV_CACHE_TYPE=q8_0` is actually live (section 2). The clamp will also
emit a `num_ctx_clamped` finding (severity `warn`) whenever it reduces a
request — visible on the **Findings** dashboard (`/d/findings`).

> The Windows _Shared Usage_ (spill) counter itself is **not** in Prometheus —
> `windows_exporter` ships no GPU collector — so the headroom panel is a
> pre-spill **early-warning** derived from dedicated-VRAM use, not a direct read
> of the spill pool. Surfacing the spill counter is a future exporter task; for
> now the PowerShell counter in "Confirming it on your box" is the ground truth.
