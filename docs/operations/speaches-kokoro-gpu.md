# Speaches: keeping Kokoro on the GPU

Kokoro TTS through the `poindexter-speaches` sidecar ran at one of two speeds,
about 1,000 characters a second or about 85, and switched between them only
when the container restarted. The cause was a coin flip inside speaches
v0.8.1, taken once per process. Pinning `PYTHONHASHSEED` in
`docker-compose.local.yml` makes the flip land on the GPU every time.

## Symptom

- The same script took eight to ten times longer from one day to the next.
  The Stage-1 podcast read (since removed, stack#4053) sent 15,000-23,000
  characters in one request. On GPU days speaches finished it in 15-29 s, for
  example 18,867 characters in 17.9 s on 2026-09-10. On CPU days it took
  114-158 s, and on 2026-09-25 it took **319 s for 22,599 characters
  (71 chars/s)**. The CPU speed also drops further when other work is using
  the CPU.
- The slow speed held for days and then flipped back. Nothing about speaches'
  image or settings changed at the flips. The one thing they had in common is
  that the container had just started.
- Nothing errored. Every request returned 200 with correct audio.

## Cause

speaches v0.8.1 (the pinned digest) builds Kokoro's onnxruntime session in
`executors/kokoro/model_manager.py` like this:

```python
available_providers = set(get_available_providers())
available_providers = available_providers - ORT_PROVIDERS_BLACKLIST
inf_sess = InferenceSession(model_files.model, providers=list(available_providers))
```

Three facts together make this a coin flip:

1. **A set of strings iterates in an order fixed by the process's hash seed.**
   Python picks a random seed at every interpreter start unless
   `PYTHONHASHSEED` is set. Inside the image, seeds 0-15 put
   `CUDAExecutionProvider` first 7 times and `CPUExecutionProvider` first
   9 times.
2. **onnxruntime reads `providers` as a priority order.** Each node in the graph
   goes to the first provider that supports it. The CPU provider supports every
   node, so when it is listed first CUDA gets nothing.
   `session.get_providers()` still lists both, so nothing looks wrong.
3. **The draw holds for the life of the process.** Kokoro unloads after
   `WHISPER__TTL` (speaches v0.8.1 applies the Whisper TTL to Kokoro too) and
   reloads on the next request, but every reload in the same process reads the
   same set in the same order. Only a new process draws again.

Whisper captions are not affected. faster-whisper runs on CTranslate2, which
picks its device from `WHISPER__INFERENCE_DEVICE`, not from this list.

## Evidence

Kokoro timed on the pinned image (2026-09-25; `bf_emma`, a 506-character
paragraph, and ten copies of it, each after one untimed warm-up call):

| Provider order                              | Session load | 506 chars        | 5,069 chars        | Model VRAM                       |
| ------------------------------------------- | ------------ | ---------------- | ------------------ | -------------------------------- |
| `CUDAExecutionProvider` first               | 3.9 s        | 0.53 s (947 c/s) | 4.72 s (1,073 c/s) | CUDA arena grew to 6.8 GB        |
| `CPUExecutionProvider` first                | 0.7 s        | 6.04 s (84 c/s)  | 60.1 s (84 c/s)    | none (0.57 GB CUDA context only) |
| Live prod process, started 2026-09-24 20:00 | 0.74 s       | 6.00 s (84 c/s)  |                    | none (534 → 664 MiB)             |

The live process was on the CPU. Loki holds speaches' own
`Generated audio for N characters in X s` line back to 2026-08-26. Grouping
requests of 1,000+ characters by process gives this:

| Process start (UTC) | Kokoro regime | Rate on 1,000+ char reads |
| ------------------- | ------------- | ------------------------- |
| before 2026-08-26   | GPU           | 574 c/s                   |
| 2026-08-27 19:53    | CPU           | 130 c/s                   |
| 2026-08-28 02:16    | GPU           | 741 c/s                   |
| 2026-08-28 04:11    | GPU           | 995 c/s                   |
| 2026-08-29 03:08    | CPU           | 104 c/s                   |
| 2026-09-01 15:49    | GPU           | 883 c/s                   |
| 2026-09-03 13:52    | GPU           | 1,056 c/s                 |
| 2026-09-06 19:39    | CPU           | 142 c/s                   |
| 2026-09-10 02:40    | GPU           | 1,055 c/s                 |
| 2026-09-11 01:00    | CPU           | 116 c/s                   |
| 2026-09-14 10:43    | GPU           | 1,054 c/s                 |
| 2026-09-15 04:54    | GPU           | 1,031 c/s                 |
| 2026-09-15 12:42    | GPU           | 779 c/s                   |
| 2026-09-15 18:40    | GPU           | 1,011 c/s                 |
| 2026-09-20 06:34    | GPU           | 1,026 c/s                 |
| 2026-09-21 05:47    | CPU           | 144 c/s                   |
| 2026-09-21 20:47    | CPU           | 120 c/s                   |
| 2026-09-23 22:36    | CPU           | 134 c/s                   |
| 2026-09-24 18:50    | CPU           | 101 c/s                   |
| 2026-09-24 20:00    | CPU           | 75 c/s                    |

Fifteen more starts had no read long enough to time. Every process stayed
in one regime for its whole life, the regime changed only at a start, and the
split (11 GPU, 9 CPU) is what a coin gives.

**Ruled out: CUDA running out of memory and falling back to CPU.**
onnxruntime does fall back when the CUDA provider fails, at session creation
or during a run, and says so on stdout with an `EP Error ... Falling back to
['CPUExecutionProvider'] and retrying.` banner. That banner appears nowhere in
speaches' logs since 2026-08-26. Every process also kept one regime across
all of its reloads: 45 in the 2026-08-29 process, 56 in the 2026-09-15 18:40
one. A fallback would follow the VRAM free at each load, not hold for the life
of the process.

## Fix

`docker-compose.local.yml` sets `PYTHONHASHSEED: "0"` on the speaches
service. With a fixed seed the set iterates the same way at every start, and
seed 0 puts CUDA first in this image. That was checked with the image's own
Python 3.12.10, and CPython 3.12 and 3.13 agree on all 64 seeds tried.

Checked end to end on a throwaway container of the pinned image with the seed
set (2026-09-25): 5,069 characters in 4.8 s (1,052 c/s) and 17,237 characters
in 16.0 s (1,079 c/s), against 114-158 s for reads that size in the CPU
regime. The process's CUDA arena peaked at 6.9 GB.

`tests/unit/infrastructure/test_speaches_kokoro_provider_order.py` pins four
things:

- The speaches image is referenced by digest, and the digest is one whose
  Kokoro order was checked (`KOKORO_ORDER_CHECKED`).
- An image that still orders providers through a set carries the seed checked
  for it.
- Re-running speaches' own expression under that seed puts CUDA first, and
  different seeds really do disagree.
- CUDA's JIT cache sits on a bind mount (next section).

Moving the digest fails the first check until someone repeats the recipe
below. That is the only moment the answer can change.

### The first read after a recreate: CUDA's JIT cache

On the GPU, the first Kokoro read in a fresh container took **75 s for 506
characters**, which take 0.5 s once warm. The image's CUDA libraries predate
the 5090 (sm_120), so the driver compiles their kernels from PTX the first time
each one runs, and writes the result to a JIT cache (199 MB for Kokoro).
Whisper's first transcription compiles into the same cache. The default cache,
`~/.nv/ComputeCache`, is inside the container: `docker restart` keeps it, but a
recreate (any compose change, image bump or deploy recreate) throws it away.

The compose file therefore sets `CUDA_CACHE_PATH` to
`/cache/huggingface/cuda-jit/speaches`, on the HF cache mount that is already
persistent and owned by the container user. It also sets `CUDA_CACHE_MAXSIZE`
to 1 GiB, so the two engines' kernels (~250 MB together) never evict each
other. With a cache kept from an earlier container, a brand-new container's
first read took 1.2 s. The first container to run with the new path still pays
the compile once, unless the cache is seeded:

```bash
mkdir -p ~/.cache/huggingface/cuda-jit/speaches
docker cp poindexter-speaches:/home/ubuntu/.nv/ComputeCache/. ~/.cache/huggingface/cuda-jit/speaches/
```

A fixed seed also switches off Python's hash randomisation, which defends
against hash-flooding requests from untrusted clients. speaches is reachable
only from the compose network and the host's port 8001 on the LAN and
tailnet, not from the internet, so that trade is acceptable here.

## Checking which device Kokoro is on

1. **From speaches' own log:** real reads (1,000+ characters) take about
   1 ms per character on the GPU and about 12 ms on the CPU.

   ```bash
   docker logs poindexter-speaches 2>&1 | grep "Generated audio for"
   ```

   Don't judge by the CI suite's requests. Tests used to post strings like
   `"A" * 500` (the egress guard has blocked them since stack#4036). A single
   500-letter "word" has almost no phonemes, so those requests look fast even
   on the CPU.

2. **By timing one request** and watching the speaches process on the card:

   ```bash
   curl -s -o /dev/null -w "%{time_total}s\n" http://localhost:8001/v1/audio/speech \
     -H 'Content-Type: application/json' \
     -d '{"model":"speaches-ai/Kokoro-82M-v1.0-ONNX","voice":"bf_emma","input":"<~500 characters of prose>","response_format":"wav"}'
   nvidia-smi --query-compute-apps=pid,used_memory --format=csv
   ```

   About 0.5-1 s means the GPU, and the speaches process grows by a GB or more
   (the onnxruntime CUDA arena). About 6 s means the CPU, and the process stays
   at its ~0.5 GB CUDA context.

3. **Re-checking a new image** (the recipe the tripwire test asks for).
   Replace `<new-digest>` with the digest you are moving to:

   ```bash
   docker run --rm --entrypoint "" ghcr.io/speaches-ai/speaches@<new-digest> sh -c '
     grep -n "providers=" /home/ubuntu/speaches/src/speaches/executors/kokoro/model_manager.py
     for s in 0 1 2 3; do PYTHONHASHSEED=$s /home/ubuntu/speaches/.venv/bin/python -c "
   from onnxruntime import get_available_providers as g
   print($s, list(set(g()) - {\"TensorrtExecutionProvider\"}))"; done'
   ```

   If `providers=` receives `list(available_providers)` built from a `set`,
   record the seed that prints `CUDAExecutionProvider` first. If the file sorts
   providers (speaches >= v0.8.2), record `None` and drop `PYTHONHASHSEED`.

## Side effects of Kokoro being back on the GPU

- **VRAM.** A GPU session holds an onnxruntime CUDA arena until
  `WHISPER__TTL` unloads it, 60 s after the last request. In the benchmark
  above the arena reached 6.8 GB after one 5,069-character read. The ~2.1 GB
  that speaches held through the 2026-09-23 hero waits (see
  `docs/architecture/video-render-vram-gate.md`) was measured while that
  process ran Kokoro on the CPU, so none of it was Kokoro.
- **Who uses Kokoro here.** On the operator stack, nothing in the pipeline
  reads with Kokoro since stack#4053 removed the Stage-1 read. Podcast and
  video narration go to Chatterbox (`podcast_tts_engine=chatterbox`, and both
  personas use `voice_provider=chatterbox`). Kokoro is still the default
  engine: an install that leaves `podcast_tts_engine` empty narrates every
  podcast and video through it.

## Moving past v0.8.1

speaches v0.8.2 sorts the providers (speaches-ai/speaches@8b54f5cf8,
`provider_priority = {"CUDAExecutionProvider": 100}`), which makes the seed
unnecessary. It also accepts CUDA provider options through
`UNSTABLE_ORT_OPTS__PROVIDER_OPTS`, which is where an arena cap would go. The
same release range turns Whisper's VAD filter on by default, as a private
setting the environment cannot change, although a transcription request can
still send `vad_filter=false`. It also moves the base image to CUDA 12.9.1.
The upgrade therefore needs a caption check before it ships. It is tracked
separately from this fix.
