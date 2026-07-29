# TTS sidecar assets

## Pinning a Chatterbox production voice (optional)

Chatterbox clones zero-shot from a reference clip, but this is **optional**:
leave it unset and every render uses Chatterbox's own built-in default voice.
Pin one only if you want a specific voice identity for the live podcast
pipeline (`podcast_tts_engine=chatterbox`).

The reference clip is **never committed** — it's an audio asset, and the right
clip is an operator choice. It doesn't even live in this directory: it's a
host-mounted operator asset, kept out of git entirely (like `bootstrap.toml`).

**Record yourself talking, not reading.** Reading aloud makes you match tone
and emphasis to the text, which clones as _narration_ rather than as you. An
unscripted clip of ordinary speech carries far more of your identity, even
though it is less tidy. Pick the passage that sounds most like you — see
"Pacing" below for why you should NOT pick it for speed.

1. Convert your clip to a clean mono reference (24 kHz suits Chatterbox's
   native sample rate; 7–10s is the sweet spot). Cut on silence at both ends
   so the clip never starts or ends mid-word, and normalize the peak to leave
   a little headroom:

       ffmpeg -i your_voice.wav -ss 21.1 -t 8.8 \
         -ac 1 -ar 24000 -af "volume=1.9dB" \
         ~/.poindexter/tts-voices/podcast-voice.wav

   `ffmpeg -i in.wav -af silencedetect=noise=-35dB:d=0.4 -f null -` prints the
   silence boundaries to cut on.

2. `docker-compose.local.yml` / `docker-compose.consumer.yml` already mount
   `~/.poindexter/tts-voices` (read-only) into the chatterbox container at
   `/app/voices`. It is a bind mount, so a new or changed file is visible to
   the sidecar immediately — **no restart needed**. (The model is loaded once
   and cached, but the reference clip is re-read per request.) Confirm with:

       docker exec poindexter-chatterbox ls -la /app/voices/

3. Point the pipeline at it:

       poindexter settings set plugin.tts_provider.chatterbox.audio_prompt_path /app/voices/podcast-voice.wav
       poindexter settings set podcast_tts_engine chatterbox

Verify with `poindexter media tts-bakeoff --engines chatterbox` — the
rendered take should carry your reference voice's identity.

## Pacing — do not try to fix this by re-recording

A clone inherits **timbre** from the reference but largely **not** its
speaking rate; Chatterbox normalizes pace toward its own preference. Measured
2026-07-28 with two references from the same speaker:

| reference      | clone @ cfg_weight 0.30 |
| -------------- | ----------------------- |
| 194 wpm        | 212 wpm                 |
| 157 wpm (−19%) | 197 wpm (−7%)           |

Only about a third of the change survived. Deliberately speaking slower is
therefore a poor pace control, and it costs you the natural delivery that
makes the clone sound like you in the first place. Two dials that DO work:

1. `plugin.tts_provider.chatterbox.cfg_weight` — lower is slower, but it
   **bottoms out around 0.30**. Below that it stretches pauses instead of
   slowing articulation, which sounds hesitant rather than measured.
2. `plugin.tts_provider.chatterbox.atempo` — pitch-preserving playback rate,
   `1.0` = off, `<1` = slower. This is the one that moves output pace
   proportionally. It rides the existing loudnorm pass, so it costs no extra
   transcode. Around `0.88`–`0.92` takes a typical clone into the 150–165 wpm
   range that reads as conversational narration.

So: choose the reference for identity, then set pace with `atempo`.

       poindexter settings set plugin.tts_provider.chatterbox.cfg_weight 0.30
       poindexter settings set plugin.tts_provider.chatterbox.atempo 0.92

To measure a take rather than guess, count words against speech time
(excluding pauses, which `silencedetect` reports):

       ffmpeg -i take.wav -af silencedetect=noise=-40dB:d=0.15 -f null -

## Multi-GPU hosts: pinning which card Chatterbox uses (optional)

The chatterbox sidecar requests one GPU without specifying which — Docker
hands it whatever it enumerates as device 0. On a host with more than one
GPU, that's not necessarily the GPU you want: if device 0 is newer than the
installed torch build has compiled kernels for, model load fails with

    RuntimeError: CUDA error: no kernel image is available for execution on the device

If you hit this, add to `~/.poindexter/bootstrap.toml`:

    chatterbox_gpu_device_id = "1"   # whichever index nvidia-smi shows as compatible

then recreate the sidecar:

    docker compose --profile tts-hq up -d --force-recreate chatterbox

`nvidia-smi --query-gpu=index,name,compute_cap --format=csv` lists each
GPU's index and compute capability — pick one your torch version supports.
