# Image-Model Bake-Off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated, throwaway harness that generates a fixed prompt set across 12 open text-to-image models, scores each on OCR text-leakage / quality / VRAM / latency / license, and produces contact sheets + a results table to pick a replacement/supplement for Z-Image-Turbo.

**Architecture:** A standalone `scripts/image_bakeoff.py` run from its own venv with an up-to-date `diffusers`. Pure logic (fair-fight config, OCR scoring, contact-sheet layout, results aggregation) is unit-tested; the GPU-bound model runner is smoke-verified on one model then run across the full roster. The live production image-gen server is never imported or touched.

**Tech Stack:** Python 3.11, PyTorch (Blackwell/5090-capable CUDA build), `diffusers` (latest), `transformers`, `accelerate`, `sentencepiece`, `Pillow`, `easyocr`, `pytest`.

## Global Constraints

- **Never import or call the live image-gen server** (`scripts/image-gen-server.py`) or its process. Isolation is the whole point.
- **Target the 5090 only** (`cuda:0` is the 5090 in this box); the 3090 stays vision-pinned. Do not change GPU pins.
- **OOM / load failure is recorded, not fatal** — a model that can't fit is bake-off data, not a crash.
- **`scripts/` one-off** — this is throwaway scaffolding, not a `services/` capability. No new production wiring.
- **Fair fight:** each model runs at its own best config (see Task 2). Never compare a CFG model with negatives off against Z-Image.
- **Model entry shape mirrors the server's `ModelConfig`** (`scripts/image-gen-server.py`) so the winner's config drops into the live `REGISTRY` later.
- **Results dir is git-ignored** — images/sheets are not committed (only the final summary table lands in the PR).
- **No secrets in code** — HF auth comes from the ambient `HF_TOKEN` / `huggingface-cli login` already present for `mattyglads`.

---

## File Structure

- `scripts/image_bakeoff.py` — the harness: roster config, fair-fight resolver, OCR scorer, contact-sheet assembler, results aggregation, model runner, CLI orchestrator. Single focused script (one-off convention).
- `scripts/image_bakeoff_prompts.json` — the fixed prompt set (real offenders + text-tempting + neutral controls), written in Task 6.
- `tests/scripts/test_image_bakeoff.py` — unit tests for the pure logic (resolver, scorer, sheet math, aggregation).
- `.gitignore` — add the results dir.

Pure helpers and the integration runner live in one script because they change together and it's a one-off; the pure ones are still independently testable via direct import.

---

## Task 1: Isolated venv + GPU smoke

**Files:**

- Create: `scripts/image_bakeoff.requirements.txt`

**Interfaces:**

- Produces: a working `.venv-bakeoff` interpreter that imports `torch` + `diffusers` and sees the 5090. Later tasks run `pytest` and the harness inside it.

- [ ] **Step 1: Capture the host's working torch build**

The host image-gen env already runs Z-Image on the 5090, so its torch is Blackwell-capable. Read its install source so we match it:

Run: `pip show torch 2>NUL & python -c "import torch;print(torch.__version__, torch.version.cuda)"`
Expected: a version like `2.7.x` / cuda `12.8` (copy the exact index-url in Step 2 to match).

- [ ] **Step 2: Write the requirements file**

Create `scripts/image_bakeoff.requirements.txt`:

```
--extra-index-url https://download.pytorch.org/whl/cu128
torch
torchvision
diffusers @ git+https://github.com/huggingface/diffusers.git
transformers
accelerate
sentencepiece
protobuf
Pillow
easyocr
pytest
```

(`diffusers` from git main because FLUX.2 / Chroma1 / CogView4 / Lumina2 / Krea2 pipelines may not be in the latest release. Adjust the `cu128` index to match Step 1's exact CUDA.)

- [ ] **Step 3: Create the venv and install**

Run:

```
python -m venv .venv-bakeoff
.venv-bakeoff\Scripts\python -m pip install --upgrade pip
.venv-bakeoff\Scripts\python -m pip install -r scripts/image_bakeoff.requirements.txt
```

Expected: installs complete without error (torch pulls the cu128 wheel).

- [ ] **Step 4: Smoke-verify torch sees the 5090 and diffusers imports**

Run:

```
.venv-bakeoff\Scripts\python -c "import torch, diffusers; print(torch.cuda.get_device_name(0)); print('diffusers', diffusers.__version__)"
```

Expected: prints `NVIDIA GeForce RTX 5090` (or the 5090's exact name) and a diffusers dev version. If it prints the 3090, STOP — fix the device index before continuing.

- [ ] **Step 5: Ignore the venv + results dir, commit the requirements**

Add to `.gitignore`:

```
.venv-bakeoff/
scripts/image_bakeoff_results/
```

Run:

```
git add scripts/image_bakeoff.requirements.txt .gitignore
git commit -m "chore(bakeoff): isolated venv requirements + gitignore"
```

---

## Task 2: Roster config + fair-fight resolver

**Files:**

- Create: `scripts/image_bakeoff.py`
- Test: `tests/scripts/test_image_bakeoff.py`

**Interfaces:**

- Produces:
  - `@dataclass(frozen=True) BakeoffModel` with fields `key:str, repo:str, pipeline_cls:str, mechanism:str ("cfg"|"distilled"), steps:int, guidance:float, dtype:str ("bfloat16"|"float16"), license_tier:str, gated:bool, notes:str`.
  - `ROSTER: list[BakeoffModel]` — the 12 models.
  - `resolve_generate_kwargs(m: BakeoffModel, prompt: str) -> dict` — the fair-fight kwargs.
  - `NEG_PROMPT: str`, `NO_TEXT_CLAUSE: str`.

- [ ] **Step 1: Write the failing test**

Create `tests/scripts/test_image_bakeoff.py`:

```python
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "image_bakeoff", Path(__file__).resolve().parents[2] / "scripts" / "image_bakeoff.py"
)
bakeoff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bakeoff)


def _model(mechanism, guidance=4.5):
    return bakeoff.BakeoffModel(
        key="x", repo="org/x", pipeline_cls="XPipeline", mechanism=mechanism,
        steps=8, guidance=guidance, dtype="bfloat16", license_tier="apache",
    )


def test_cfg_model_gets_negative_prompt_and_guidance():
    kw = bakeoff.resolve_generate_kwargs(_model("cfg"), "a server room")
    assert kw["negative_prompt"] == bakeoff.NEG_PROMPT
    assert kw["guidance_scale"] == 4.5
    assert kw["prompt"] == "a server room"  # unmodified


def test_distilled_model_gets_no_text_clause_and_zero_guidance():
    kw = bakeoff.resolve_generate_kwargs(_model("distilled"), "a server room")
    assert "negative_prompt" not in kw
    assert kw["guidance_scale"] == 0.0
    assert kw["prompt"].endswith(bakeoff.NO_TEXT_CLAUSE)
    assert kw["prompt"].startswith("a server room")


def test_roster_has_twelve_unique_keys():
    keys = [m.key for m in bakeoff.ROSTER]
    assert len(keys) == 12
    assert len(set(keys)) == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -v`
Expected: FAIL — `image_bakeoff.py` does not exist / has no `BakeoffModel`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/image_bakeoff.py`:

```python
"""Image-model bake-off harness (throwaway one-off).

Generates a fixed prompt set across a roster of open text-to-image models,
scores OCR text-leakage / VRAM / latency, and assembles contact sheets +
a results table. Runs in its own venv; never touches the live image-gen server.

See docs/superpowers/specs/2026-07-12-image-model-bakeoff-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass


NEG_PROMPT = (
    "text, words, letters, numbers, signage, UI, labels, "
    "watermark, captions, writing, logo"
)
NO_TEXT_CLAUSE = "clean textless composition, no text, no signage, no UI, no labels"


@dataclass(frozen=True)
class BakeoffModel:
    key: str
    repo: str
    pipeline_cls: str            # diffusers pipeline class name
    mechanism: str               # "cfg" | "distilled"
    steps: int
    guidance: float              # used only for CFG models
    dtype: str                   # "bfloat16" | "float16"
    license_tier: str            # "apache" | "mit" | "community" | "custom"
    gated: bool = False
    notes: str = ""


# NOTE: repo IDs / pipeline_cls for brand-new models are verified in Task 6's
# dry run; trim any whose pipeline isn't importable from diffusers git main.
ROSTER: list[BakeoffModel] = [
    BakeoffModel("sdxl_lightning", "stabilityai/stable-diffusion-xl-base-1.0",
                 "StableDiffusionXLPipeline", "distilled", 4, 0.0, "float16",
                 "openrail", notes="old baseline; +SDXL-Lightning 4-step LoRA"),
    BakeoffModel("z_image_turbo", "Tongyi-MAI/Z-Image-Turbo",
                 "ZImagePipeline", "distilled", 9, 0.0, "bfloat16", "apache",
                 notes="current control"),
    BakeoffModel("chroma1_hd", "lodestones/Chroma1-HD",
                 "ChromaPipeline", "cfg", 26, 4.5, "bfloat16", "apache"),
    BakeoffModel("chroma1_flash", "lodestones/Chroma1-Flash",
                 "ChromaPipeline", "distilled", 8, 0.0, "bfloat16", "apache"),
    BakeoffModel("cogview4", "zai-org/CogView4-6B",
                 "CogView4Pipeline", "cfg", 50, 3.5, "bfloat16", "apache"),
    BakeoffModel("lumina2", "Alpha-VLLM/Lumina-Image-2.0",
                 "Lumina2Pipeline", "cfg", 30, 4.0, "bfloat16", "apache"),
    BakeoffModel("flux2_klein", "black-forest-labs/FLUX.2-klein-4B",
                 "Flux2Pipeline", "distilled", 8, 0.0, "bfloat16", "apache",
                 notes="mechanism verified in dry run"),
    BakeoffModel("hidream_fast", "HiDream-ai/HiDream-I1-Fast",
                 "HiDreamImagePipeline", "distilled", 16, 0.0, "bfloat16", "mit"),
    BakeoffModel("qwen_image", "Qwen/Qwen-Image",
                 "QwenImagePipeline", "cfg", 30, 4.0, "bfloat16", "apache"),
    BakeoffModel("flux1_schnell", "black-forest-labs/FLUX.1-schnell",
                 "FluxPipeline", "distilled", 4, 0.0, "bfloat16", "apache",
                 gated=True),
    BakeoffModel("sd35_medium", "stabilityai/stable-diffusion-3.5-medium",
                 "StableDiffusion3Pipeline", "cfg", 40, 4.5, "bfloat16",
                 "community", gated=True),
    BakeoffModel("krea2_turbo", "krea/Krea-2-Turbo",
                 "Krea2Pipeline", "distilled", 8, 0.0, "bfloat16", "custom",
                 gated=True, notes="eval-only; will not ship in OSS"),
]


def resolve_generate_kwargs(m: BakeoffModel, prompt: str) -> dict:
    """Fair-fight generate kwargs.

    CFG models get the real negative prompt at their guidance; distilled
    models get guidance 0 + a positive no-text clause (the only steering a
    guidance-free model responds to).
    """
    if m.mechanism == "cfg":
        return {
            "prompt": prompt,
            "negative_prompt": NEG_PROMPT,
            "num_inference_steps": m.steps,
            "guidance_scale": m.guidance,
        }
    return {
        "prompt": f"{prompt}, {NO_TEXT_CLAUSE}",
        "num_inference_steps": m.steps,
        "guidance_scale": 0.0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/image_bakeoff.py tests/scripts/test_image_bakeoff.py
git commit -m "feat(bakeoff): roster config + fair-fight generate-kwargs resolver"
```

---

## Task 3: OCR text-leakage scorer

**Files:**

- Modify: `scripts/image_bakeoff.py`
- Test: `tests/scripts/test_image_bakeoff.py`

**Interfaces:**

- Produces: `count_text_chars(image_path: str, reader) -> int` — total detected characters at confidence ≥ 0.3. `reader` is any object with `.readtext(path, detail=1) -> list[(box, text, conf)]` (real easyocr `Reader` in the run; a fake in tests).

- [ ] **Step 1: Write the failing test**

Append to `tests/scripts/test_image_bakeoff.py`:

```python
class _FakeReader:
    def __init__(self, results):
        self._results = results

    def readtext(self, path, detail=1):
        return self._results


def test_count_text_chars_sums_confident_detections():
    reader = _FakeReader([("b", "HELLO", 0.9), ("b", "world", 0.8)])
    assert bakeoff.count_text_chars("x.png", reader) == 10


def test_count_text_chars_filters_low_confidence():
    reader = _FakeReader([("b", "HELLO", 0.9), ("b", "zz", 0.1)])
    assert bakeoff.count_text_chars("x.png", reader) == 5


def test_count_text_chars_zero_on_no_detections():
    assert bakeoff.count_text_chars("x.png", _FakeReader([])) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -k count_text -v`
Expected: FAIL — `count_text_chars` not defined.

- [ ] **Step 3: Implement**

Append to `scripts/image_bakeoff.py`:

```python
def count_text_chars(image_path: str, reader) -> int:
    """Total detected text characters in an image (lower = better).

    `reader` is an easyocr.Reader (or any object exposing
    `.readtext(path, detail=1) -> list[(box, text, confidence)]`).
    Confidence < 0.3 detections are dropped as noise.
    """
    detections = reader.readtext(image_path, detail=1)
    return sum(
        len(text) for _box, text, conf in detections if conf >= 0.3
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -k count_text -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Real easyocr smoke (manual, once)**

Run:

```
.venv-bakeoff\Scripts\python -c "import easyocr, PIL.Image, PIL.ImageDraw; img=PIL.Image.new('RGB',(400,120),'white'); PIL.ImageDraw.Draw(img).text((20,40),'BENCHMARK 42 FPS',fill='black'); img.save('t.png'); import scripts.image_bakeoff as b; r=easyocr.Reader(['en'],gpu=False); print('chars:', b.count_text_chars('t.png', r))"
```

Expected: prints a char count > 0 (roughly the length of the drawn string). Confirms easyocr is wired.

- [ ] **Step 6: Commit**

```bash
git add scripts/image_bakeoff.py tests/scripts/test_image_bakeoff.py
git commit -m "feat(bakeoff): OCR text-leakage char-count scorer"
```

---

## Task 4: Contact-sheet assembler

**Files:**

- Modify: `scripts/image_bakeoff.py`
- Test: `tests/scripts/test_image_bakeoff.py`

**Interfaces:**

- Produces: `contact_sheet_dimensions(n: int, cols: int, thumb: int, pad: int, label_h: int) -> tuple[int, int]` (canvas W, H) and `build_contact_sheet(cells: list[tuple[str, str]], out_path: str, *, cols=4, thumb=384, pad=8, label_h=24) -> str`. `cells` is `(label, image_path)`; missing/None image paths render a gray placeholder.

- [ ] **Step 1: Write the failing test**

Append to `tests/scripts/test_image_bakeoff.py`:

```python
def test_contact_sheet_dimensions_three_rows():
    # 12 cells, 4 cols -> 3 rows
    w, h = bakeoff.contact_sheet_dimensions(12, cols=4, thumb=384, pad=8, label_h=24)
    assert w == 4 * 384 + 5 * 8            # cols*thumb + (cols+1)*pad
    assert h == 3 * (384 + 24) + 4 * 8     # rows*(thumb+label) + (rows+1)*pad


def test_contact_sheet_dimensions_partial_last_row():
    # 5 cells, 4 cols -> 2 rows
    _w, h = bakeoff.contact_sheet_dimensions(5, cols=4, thumb=384, pad=8, label_h=24)
    assert h == 2 * (384 + 24) + 3 * 8


def test_build_contact_sheet_writes_file(tmp_path):
    from PIL import Image
    img_a = tmp_path / "a.png"
    Image.new("RGB", (100, 100), "red").save(img_a)
    out = tmp_path / "sheet.png"
    result = bakeoff.build_contact_sheet(
        [("model_a", str(img_a)), ("model_b", None)], str(out), cols=2,
    )
    assert result == str(out)
    assert out.exists()
    assert Image.open(out).size == bakeoff.contact_sheet_dimensions(2, 2, 384, 8, 24)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -k contact_sheet -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Append to `scripts/image_bakeoff.py`:

```python
import math

from PIL import Image, ImageDraw


def contact_sheet_dimensions(
    n: int, cols: int, thumb: int, pad: int, label_h: int
) -> tuple[int, int]:
    """Canvas (width, height) for an n-cell grid of thumb-sized cells."""
    rows = max(1, math.ceil(n / cols))
    width = cols * thumb + (cols + 1) * pad
    height = rows * (thumb + label_h) + (rows + 1) * pad
    return width, height


def build_contact_sheet(
    cells: list[tuple[str, str]],
    out_path: str,
    *,
    cols: int = 4,
    thumb: int = 384,
    pad: int = 8,
    label_h: int = 24,
) -> str:
    """Render labelled thumbnails in a grid and save to out_path.

    cells is (label, image_path); a None/missing image_path renders a gray
    placeholder so a failed model still occupies its slot.
    """
    width, height = contact_sheet_dimensions(len(cells), cols, thumb, pad, label_h)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for i, (label, path) in enumerate(cells):
        r, c = divmod(i, cols)
        x = pad + c * (thumb + pad)
        y = pad + r * (thumb + label_h + pad)
        if path:
            try:
                im = Image.open(path).convert("RGB").resize((thumb, thumb))
                canvas.paste(im, (x, y))
            except OSError:
                draw.rectangle([x, y, x + thumb, y + thumb], fill="gray")
        else:
            draw.rectangle([x, y, x + thumb, y + thumb], fill="gray")
        draw.text((x + 4, y + thumb + 4), label, fill="black")
    canvas.save(out_path)
    return out_path
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -k contact_sheet -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/image_bakeoff.py tests/scripts/test_image_bakeoff.py
git commit -m "feat(bakeoff): labelled contact-sheet assembler"
```

---

## Task 5: Run records + results aggregation

**Files:**

- Modify: `scripts/image_bakeoff.py`
- Test: `tests/scripts/test_image_bakeoff.py`

**Interfaces:**

- Produces:
  - `@dataclass RunRecord` with `model:str, prompt_id:str, image_path:str|None, text_chars:int|None, latency_s:float|None, peak_vram_gb:float|None, error:str|None=None`.
  - `summarize(records: list[RunRecord], roster: list[BakeoffModel], consumer_gb: float = 16.0) -> list[dict]` — per-model summary rows with `model, mean_text_chars, max_peak_vram_gb, mean_latency_s, consumer_ok, license_tier, n_ok, n_error`, sorted by `mean_text_chars` ascending (best first).
  - `results_markdown(summary: list[dict]) -> str` — a markdown table.

- [ ] **Step 1: Write the failing test**

Append to `tests/scripts/test_image_bakeoff.py`:

```python
def test_summarize_ranks_by_text_chars_and_flags_vram():
    roster = [
        bakeoff.BakeoffModel("a", "o/a", "P", "cfg", 8, 4.0, "bfloat16", "apache"),
        bakeoff.BakeoffModel("b", "o/b", "P", "distilled", 8, 0.0, "bfloat16", "mit"),
    ]
    records = [
        bakeoff.RunRecord("a", "p1", "a1.png", text_chars=2, latency_s=3.0, peak_vram_gb=10.0),
        bakeoff.RunRecord("a", "p2", "a2.png", text_chars=4, latency_s=5.0, peak_vram_gb=12.0),
        bakeoff.RunRecord("b", "p1", "b1.png", text_chars=40, latency_s=2.0, peak_vram_gb=20.0),
        bakeoff.RunRecord("b", "p2", None, text_chars=None, latency_s=None, peak_vram_gb=None, error="OOM"),
    ]
    summary = bakeoff.summarize(records, roster, consumer_gb=16.0)
    assert [row["model"] for row in summary] == ["a", "b"]  # a leaks less -> first
    row_a, row_b = summary
    assert row_a["mean_text_chars"] == 3.0
    assert row_a["max_peak_vram_gb"] == 12.0
    assert row_a["consumer_ok"] is True          # 12 <= 16
    assert row_b["consumer_ok"] is False         # 20 > 16
    assert row_b["n_error"] == 1


def test_results_markdown_contains_header_and_rows():
    summary = [{"model": "a", "mean_text_chars": 3.0, "max_peak_vram_gb": 12.0,
                "mean_latency_s": 4.0, "consumer_ok": True, "license_tier": "apache",
                "n_ok": 2, "n_error": 0}]
    md = bakeoff.results_markdown(summary)
    assert "| model |" in md
    assert "| a |" in md
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -k "summarize or results_markdown" -v`
Expected: FAIL — `RunRecord` / `summarize` not defined.

- [ ] **Step 3: Implement**

Append to `scripts/image_bakeoff.py`:

```python
from dataclasses import field
from statistics import mean


@dataclass
class RunRecord:
    model: str
    prompt_id: str
    image_path: str | None
    text_chars: int | None
    latency_s: float | None
    peak_vram_gb: float | None
    error: str | None = None


def summarize(
    records: list[RunRecord],
    roster: list[BakeoffModel],
    consumer_gb: float = 16.0,
) -> list[dict]:
    """Aggregate per-(model,prompt) records into per-model summary rows,
    sorted by mean text-leakage ascending (best first)."""
    by_key = {m.key: m for m in roster}
    grouped: dict[str, list[RunRecord]] = {}
    for rec in records:
        grouped.setdefault(rec.model, []).append(rec)

    rows: list[dict] = []
    for key, recs in grouped.items():
        ok = [r for r in recs if r.error is None]
        text_vals = [r.text_chars for r in ok if r.text_chars is not None]
        lat_vals = [r.latency_s for r in ok if r.latency_s is not None]
        vram_vals = [r.peak_vram_gb for r in ok if r.peak_vram_gb is not None]
        max_vram = max(vram_vals) if vram_vals else None
        rows.append({
            "model": key,
            "mean_text_chars": mean(text_vals) if text_vals else float("inf"),
            "max_peak_vram_gb": max_vram,
            "mean_latency_s": round(mean(lat_vals), 2) if lat_vals else None,
            "consumer_ok": (max_vram is not None and max_vram <= consumer_gb),
            "license_tier": by_key[key].license_tier if key in by_key else "?",
            "n_ok": len(ok),
            "n_error": len(recs) - len(ok),
        })
    rows.sort(key=lambda r: r["mean_text_chars"])
    return rows


def results_markdown(summary: list[dict]) -> str:
    """Render the per-model summary as a markdown table."""
    header = (
        "| model | mean_text_chars | max_vram_gb | consumer_ok | "
        "mean_latency_s | license | ok | err |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for r in summary:
        lines.append(
            f"| {r['model']} | {r['mean_text_chars']} | {r['max_peak_vram_gb']} | "
            f"{r['consumer_ok']} | {r['mean_latency_s']} | {r['license_tier']} | "
            f"{r['n_ok']} | {r['n_error']} |"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv-bakeoff\Scripts\python -m pytest tests/scripts/test_image_bakeoff.py -v`
Expected: PASS (all tests — resolver, OCR, sheet, summarize).

- [ ] **Step 5: Commit**

```bash
git add scripts/image_bakeoff.py tests/scripts/test_image_bakeoff.py
git commit -m "feat(bakeoff): run records + per-model results aggregation"
```

---

## Task 6: Prompt set + model runner (integration)

**Files:**

- Create: `scripts/image_bakeoff_prompts.json`
- Modify: `scripts/image_bakeoff.py`

**Interfaces:**

- Consumes: `BakeoffModel`, `resolve_generate_kwargs`, `RunRecord`.
- Produces:
  - `load_prompts(path: str) -> list[tuple[str, str]]` — `(prompt_id, prompt_text)`.
  - `load_pipeline(m: BakeoffModel)` — returns a diffusers pipeline on cuda:0, or raises.
  - `run_one_model(m, prompts, out_dir, reader) -> list[RunRecord]` — generate + score every prompt for one model; OOM/failure is captured in `RunRecord.error`, never raised.

- [ ] **Step 1: Extract the real offending prompts from the DB**

Query the actual prompts from posts that showed mangled text (adjust LIMIT/columns to what exists):

Run (via the postgres MCP or psql):

```sql
SELECT id, metadata->>'prompt' AS prompt
FROM media_assets
WHERE asset_type IN ('featured_image','inline_image')
  AND metadata->>'prompt' IS NOT NULL
ORDER BY created_at DESC
LIMIT 6;
```

Copy 4-6 real prompts into the JSON below.

- [ ] **Step 2: Write the prompt file**

Create `scripts/image_bakeoff_prompts.json` (replace the `real_*` entries with the DB results from Step 1; keep the tempting + neutral ones):

```json
[
  { "id": "real_1", "text": "<<paste real offending prompt 1>>" },
  { "id": "real_2", "text": "<<paste real offending prompt 2>>" },
  { "id": "real_3", "text": "<<paste real offending prompt 3>>" },
  {
    "id": "tempting_benchmark",
    "text": "an isometric illustration of a GPU benchmark chart comparing frame rates, stylized vector art"
  },
  {
    "id": "tempting_dashboard",
    "text": "a stylized illustration of a server monitoring dashboard with graphs, flat vector style"
  },
  {
    "id": "tempting_diagram",
    "text": "a labelled technical diagram of a gaming PC's internal components, clean line art"
  },
  {
    "id": "neutral_serverroom",
    "text": "an isometric 3D illustration of a futuristic server room, soft shadows, no text"
  },
  {
    "id": "neutral_dataflow",
    "text": "an abstract illustration of data flowing between glowing nodes, cel-shaded"
  }
]
```

- [ ] **Step 3: Implement the loader + runner**

Append to `scripts/image_bakeoff.py`:

```python
import json
import os
import time

import torch


def load_prompts(path: str) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [(row["id"], row["text"]) for row in data]


def load_pipeline(m: BakeoffModel):
    """Import the model's diffusers pipeline class and load it onto cuda:0.

    Raises on unknown class / download 403 (gated, unaccepted) / OOM — the
    caller converts the exception into a RunRecord error.
    """
    import diffusers

    if not hasattr(diffusers, m.pipeline_cls):
        raise RuntimeError(
            f"diffusers has no {m.pipeline_cls} (upgrade git main or trim {m.key})"
        )
    cls = getattr(diffusers, m.pipeline_cls)
    dtype = torch.bfloat16 if m.dtype == "bfloat16" else torch.float16
    pipe = cls.from_pretrained(m.repo, torch_dtype=dtype)
    pipe = pipe.to("cuda:0")
    return pipe


def _unload(pipe) -> None:
    del pipe
    torch.cuda.empty_cache()
    torch.cuda.synchronize()


def run_one_model(
    m: BakeoffModel,
    prompts: list[tuple[str, str]],
    out_dir: str,
    reader,
) -> list[RunRecord]:
    """Generate + score every prompt for one model. Never raises: a load or
    per-image failure is recorded as RunRecord.error and the run continues."""
    os.makedirs(out_dir, exist_ok=True)
    try:
        pipe = load_pipeline(m)
    except Exception as exc:  # noqa: BLE001 — bake-off records, never crashes
        return [
            RunRecord(m.key, pid, None, None, None, None, error=f"load: {exc}")
            for pid, _ in prompts
        ]

    records: list[RunRecord] = []
    try:
        for pid, text in prompts:
            kwargs = resolve_generate_kwargs(m, text)
            img_path = os.path.join(out_dir, f"{m.key}__{pid}.png")
            try:
                torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                image = pipe(**kwargs).images[0]
                latency = time.perf_counter() - t0
                image.save(img_path)
                peak_gb = torch.cuda.max_memory_allocated() / 1024**3
                chars = count_text_chars(img_path, reader)
                records.append(
                    RunRecord(m.key, pid, img_path, chars, latency, peak_gb)
                )
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                records.append(
                    RunRecord(m.key, pid, None, None, None, None, error="OOM")
                )
            except Exception as exc:  # noqa: BLE001
                records.append(
                    RunRecord(m.key, pid, None, None, None, None, error=str(exc))
                )
    finally:
        _unload(pipe)
    return records
```

- [ ] **Step 4: Smoke-run the runner on ONE small model**

Verify the integration path end-to-end on the lightest model (Lumina-2.0, ~2B) with a single prompt:

Run:

```
.venv-bakeoff\Scripts\python -c "import easyocr, scripts.image_bakeoff as b; m=[x for x in b.ROSTER if x.key=='lumina2'][0]; r=easyocr.Reader(['en'],gpu=False); recs=b.run_one_model(m,[('neutral_serverroom','an isometric server room, no text')],'scripts/image_bakeoff_results/smoke',r); print(recs)"
```

Expected: one `RunRecord` with a non-None `image_path`, a `text_chars` int, a `peak_vram_gb` float, `error=None`, and the PNG exists under the smoke dir. If it errors on the pipeline class, fix the `pipeline_cls`/`repo` for that model (repo-existence caveat) before proceeding.

- [ ] **Step 5: Commit**

```bash
git add scripts/image_bakeoff.py scripts/image_bakeoff_prompts.json
git commit -m "feat(bakeoff): prompt loader + GPU model runner (OOM-safe)"
```

---

## Task 7: CLI orchestrator

**Files:**

- Modify: `scripts/image_bakeoff.py`

**Interfaces:**

- Consumes: everything above.
- Produces: `main(argv=None) -> int` + `if __name__ == "__main__"` guard. Flags: `--models a,b` (default all), `--prompts PATH` (default `scripts/image_bakeoff_prompts.json`), `--out DIR` (default `scripts/image_bakeoff_results`), `--dry-run` (print the plan, no GPU).

- [ ] **Step 1: Implement the CLI**

Append to `scripts/image_bakeoff.py`:

```python
import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Image-model bake-off harness")
    parser.add_argument("--models", default="", help="comma-separated model keys; default all")
    parser.add_argument("--prompts", default="scripts/image_bakeoff_prompts.json")
    parser.add_argument("--out", default="scripts/image_bakeoff_results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    wanted = set(filter(None, args.models.split(",")))
    roster = [m for m in ROSTER if not wanted or m.key in wanted]
    prompts = load_prompts(args.prompts)

    if args.dry_run:
        print(f"Would run {len(roster)} models x {len(prompts)} prompts:")
        for m in roster:
            gate = "GATED" if m.gated else "open"
            print(f"  {m.key:16} {m.mechanism:9} {m.license_tier:10} {gate}")
        return 0

    import easyocr
    reader = easyocr.Reader(["en"], gpu=False)

    all_records: list[RunRecord] = []
    for m in roster:
        print(f"=== {m.key} ===", flush=True)
        recs = run_one_model(m, prompts, os.path.join(args.out, "images"), reader)
        for r in recs:
            flag = r.error or f"{r.text_chars} chars, {r.peak_vram_gb:.1f}GB"
            print(f"  {r.prompt_id:22} {flag}", flush=True)
        all_records.extend(recs)

    # Per-prompt contact sheets (one grid per prompt, all models side by side).
    os.makedirs(os.path.join(args.out, "sheets"), exist_ok=True)
    by_prompt: dict[str, list[tuple[str, str]]] = {}
    for r in all_records:
        by_prompt.setdefault(r.prompt_id, []).append((r.model, r.image_path))
    for pid, cells in by_prompt.items():
        build_contact_sheet(cells, os.path.join(args.out, "sheets", f"{pid}.png"))

    summary = summarize(all_records, roster)
    md = results_markdown(summary)
    with open(os.path.join(args.out, "results.md"), "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print("\n" + md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke the dry-run (no GPU)**

Run: `.venv-bakeoff\Scripts\python scripts/image_bakeoff.py --dry-run`
Expected: prints all 12 models with mechanism/license/gated flags, exits 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/image_bakeoff.py
git commit -m "feat(bakeoff): CLI orchestrator with dry-run, sheets, results table"
```

---

## Task 8: Accept gated licenses + full run + report

**Files:**

- Modify: PR #2386 body / a results comment (summary table + best sheets).

**Interfaces:** none — this is the execution + reporting task.

- [ ] **Step 1: Matt accepts the 3 gated/eval licenses**

Confirm `mattyglads` has clicked "Agree and access" on:

- https://huggingface.co/black-forest-labs/FLUX.1-schnell
- https://huggingface.co/stabilityai/stable-diffusion-3.5-medium
- https://huggingface.co/krea/Krea-2-Turbo

(Any not accepted will simply record a `load: 403` error and be skipped — not fatal.)

- [ ] **Step 2: Ungated dry pass first (cheap validation)**

Run only the open models to shake out repo/pipeline issues before the big gated downloads:

Run: `.venv-bakeoff\Scripts\python scripts/image_bakeoff.py --models sdxl_lightning,z_image_turbo,lumina2,chroma1_hd`
Expected: 4 models complete; check `scripts/image_bakeoff_results/results.md` and the sheets look sane. Fix any `pipeline_cls`/`repo` mismatches (repo-existence caveat) and re-run.

- [ ] **Step 3: Full sweep**

Run (in a quiet content window — this takes ~1-2 hrs incl. downloads):

```
.venv-bakeoff\Scripts\python scripts/image_bakeoff.py
```

Expected: all 12 models run (gated ones download once); `results.md` + per-prompt sheets written under `scripts/image_bakeoff_results/`.

- [ ] **Step 4: Report to the PR**

Post the `results.md` table + attach the 2-3 most telling contact sheets as a comment on PR #2386, with a one-line recommendation (lowest-text-leakage model that clears the 16 GB consumer tier and is Apache/MIT). Do NOT commit the images.

Run:

```bash
gh pr comment 2386 --body-file scripts/image_bakeoff_results/results.md
```

- [ ] **Step 5: Mark the winner's productionization as a follow-up**

The registry change (adding the winner's `ModelConfig` to `scripts/image-gen-server.py` + `services/image_service.py`, flipping `app_settings.image_generation_model`, `/reload`) is a **separate** change — note it in the PR as the next step, do not fold it into the bake-off harness.

---

## Self-Review

**Spec coverage:**

- Root-cause / goal → Task 8 report (recommendation). ✓
- 12-model roster → Task 2 `ROSTER`. ✓
- Fair-fight config → Task 2 `resolve_generate_kwargs`. ✓
- Prompt set (real + tempting + neutral) → Task 6 JSON + Step 1 DB query. ✓
- Scoring: text-leakage → Task 3; VRAM/latency → Task 6 runner; aggregation/consumer-tier/license → Task 5; quality (eyeball) → Task 7 sheets + Task 8. ✓
- Standalone isolated harness / own venv / 5090 → Task 1 + Global Constraints. ✓
- Outputs (sheets + table + configs) → Task 7. ✓
- Gated-license handling → Task 6 `load_pipeline` fail-soft + Task 8 Step 1. ✓
- Productionization path (separate) → Task 8 Step 5. ✓

**Placeholder scan:** The only intentional fill-ins are the `real_*` prompt strings (Task 6 Step 2), sourced from the DB query in Step 1 — real content produced during execution, not a code placeholder. `pipeline_cls`/`repo` for new models are validated in Task 6 Step 4 / Task 8 Step 2 per the spec's repo-existence caveat.

**Type consistency:** `BakeoffModel`, `RunRecord`, `resolve_generate_kwargs`, `count_text_chars`, `build_contact_sheet`/`contact_sheet_dimensions`, `summarize`/`results_markdown`, `load_prompts`/`load_pipeline`/`run_one_model`, `main` — names and signatures match across the tasks that define and consume them. `count_text_chars(path, reader)` reader-injection is consistent between Task 3 (fake) and Task 6/7 (easyocr). ✓
