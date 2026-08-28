# Diagnostics

One-off reproduction scripts kept because re-deriving them costs more than
storing them. Not wired into CI or the scheduler — run by hand when the
matching symptom appears.

| script                        | reproduces                                                                                                                                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ollama-runner-mem-bench.sh`  | A/B of an ollama runner's host-side anonymous footprint across `use_mmap` on/off, with unload/reload arms. Answers "is mmap responsible for this runner's swap footprint?" (2026-08-28: **no**). |
| `ollama-runner-leak-bench.sh` | Drives N requests at a loaded runner and plots total anonymous per batch. Distinguishes a **leak** (linear, no deceleration) from a **bounded cache** (plateaus).                                |

Both measure **total anonymous = `RssAnon` + `VmSwap`**, never `RssAnon` alone.
On a host with swap the kernel evicts an untouched leak within minutes, so
`RssAnon` falls back to ~10 MiB and the process reads as clean while holding
9 GiB. That is precisely how the 2026-08-28 leak stayed invisible.

Both restore the pinned production state on exit (`trap`), and both cost an
~85 s model reload per arm — run them when the box is quiet.

Findings: [`docs/operations/host-oom-protection.md`](../../docs/operations/host-oom-protection.md).
