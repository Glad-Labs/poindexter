# Diagnostics

One-off reproduction scripts kept because re-deriving them costs more than
storing them. Not wired into CI or the scheduler — run by hand when the
matching symptom appears.

| script                                | reproduces                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ollama-runner-mem-bench.sh`          | A/B of an ollama runner's host-side anonymous footprint across `use_mmap` on/off, with unload/reload arms. Answers "is mmap responsible for this runner's swap footprint?" (2026-08-28: **no**).                                                                                                                                                                                                  |
| `ollama-runner-prompt-cache-bench.sh` | What one request costs the runner's host-RAM prompt cache, by shape (repeat / short text / long text / image), `/proc` beside the runner's own logged KV size. Its `repeat` arm tells a **cache** (a repeat adds nothing) from a **leak** (every request grows it). Replaced `ollama-runner-leak-bench.sh`, whose 30 short requests never reached the cache's 8 GiB cap and so read it as a leak. |

Both measure **total anonymous = `RssAnon` + `VmSwap`**, never `RssAnon` alone.
On a host with swap the kernel evicts untouched memory within minutes, so
`RssAnon` falls back to ~10 MiB and the process reads as clean while holding
9 GiB. That is precisely how the 2026-08-28 growth stayed invisible.

The mem bench restores the pinned production state on exit (`trap`) and costs
a model reload per arm. The prompt-cache bench reloads nothing (it refuses to
run unless the model is resident at `NUM_CTX`, the pinned context), but it
adds ~2 GiB of cache entries; `RESTORE=1` unloads and re-pins at the end. Run
either when the box is quiet — the prompt-cache bench flags an arm that
another client's requests contaminated.

Findings: [`docs/operations/host-oom-protection.md`](../../docs/operations/host-oom-protection.md).
