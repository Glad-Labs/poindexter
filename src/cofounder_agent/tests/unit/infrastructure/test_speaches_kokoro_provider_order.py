"""speaches must start Kokoro on the GPU every time, not on a coin flip.

speaches v0.8.1 builds Kokoro's onnxruntime session like this::

    available_providers = set(get_available_providers())
    available_providers = available_providers - ORT_PROVIDERS_BLACKLIST
    InferenceSession(model, providers=list(available_providers))

A set of strings iterates in an order fixed by the interpreter's hash seed,
and that seed is random per process unless ``PYTHONHASHSEED`` pins it.
onnxruntime reads ``providers`` as a priority list, and whichever of CUDA and
CPU comes first takes every node. So each container start was a coin flip,
and the result held until the next restart. A start that drew CPU ran Kokoro
at 84 chars/s instead of ~1,000 on the 5090 (both measured on the pinned image
on 2026-09-25). Of the 20 speaches processes between 2026-08-26 and
2026-09-25 that had TTS to measure, 9 ran Kokoro on the CPU. That was the
"slow regime" that lasted days and then flipped back.

``docker-compose.local.yml`` pins the seed. A seed is only right for the image
it was checked against, so this file pins the pair. The speaches digest must
be one whose Kokoro order was checked, and an image that still orders
providers through a set must carry the seed checked for it. Moving the digest
fails here until someone re-checks, which is the one moment the answer can
change. The seed's effect is re-derived below by running speaches' own
expression, not read back from the table.

Back on the GPU, Kokoro's first read in a fresh container waits ~75 s while
CUDA compiles kernels the image's libraries don't ship for the 5090, so the
last test keeps CUDA's JIT cache on a mount that outlives the container. See
``docs/operations/speaches-kokoro-gpu.md`` for the diagnosis and the recipe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
COMPOSE_FILE = REPO_ROOT / "docker-compose.local.yml"
SERVICE = "speaches"
IMAGE_REPO = "ghcr.io/speaches-ai/speaches"

# What ``onnxruntime.get_available_providers()`` returns inside the pinned
# image (onnxruntime-gpu 1.20.1), in order. The order matters: it is the
# insertion order of the set, which decides ties between colliding hashes.
IMAGE_PROVIDERS = [
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "CPUExecutionProvider",
]
ORT_PROVIDERS_BLACKLIST = {"TensorrtExecutionProvider"}

# speaches digest -> the PYTHONHASHSEED checked inside that image to put
# CUDAExecutionProvider first, or None for an image that sorts the providers
# itself (speaches >= v0.8.2, speaches-ai/speaches@8b54f5cf8) and needs no seed.
KOKORO_ORDER_CHECKED = {
    # speaches v0.8.1: its executors/kokoro/model_manager.py and config.py are
    # md5-identical to the v0.8.1 tag. Checked 2026-09-25 with the image's own
    # Python 3.12.10: seed 0 -> ['CUDAExecutionProvider', 'CPUExecutionProvider'].
    "sha256:6ec12ebf890a17e0d4b242a8ba9e0eb1fb836e60e8a3c857aea9838d541579ac": "0",
}

# Seeds used to show the order really depends on the seed. The image's Python
# 3.12.10 and CPython 3.13 give the same order for seeds 0-63 (checked
# 2026-09-25), and 16 is enough to see both orders.
PROBE_SEEDS = range(16)


def _speaches_service() -> dict:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    service = (compose.get("services") or {}).get(SERVICE)
    assert service, f"{COMPOSE_FILE.name} has no {SERVICE!r} service"
    return service


def _image_digest(service: dict) -> str:
    image = str(service.get("image") or "")
    repo, sep, digest = image.partition("@")
    assert sep and repo == IMAGE_REPO and digest.startswith("sha256:"), (
        f"{SERVICE} runs {image!r}. It must be {IMAGE_REPO}@sha256:<digest>. "
        "A tag can move under the pin, and the Kokoro order below was checked "
        "for one exact image."
    )
    return digest


def _environment(service: dict) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs = (str(item).partition("=") for item in env)
        return {key: value for key, _, value in pairs}
    return {str(key): str(value) for key, value in env.items()}


def _v081_provider_order(seed: str) -> list[str]:
    """Run speaches v0.8.1's provider expression under ``PYTHONHASHSEED=seed``.

    The seed only takes effect at interpreter start, so it needs a fresh
    process. The expression is copied from executors/kokoro/model_manager.py.
    """
    code = (
        "import json, sys\n"
        "available = set(json.loads(sys.argv[1]))\n"
        "available = available - set(json.loads(sys.argv[2]))\n"
        "print(json.dumps(list(available)))\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            json.dumps(IMAGE_PROVIDERS),
            json.dumps(sorted(ORT_PROVIDERS_BLACKLIST)),
        ],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


def test_the_speaches_image_is_one_whose_kokoro_order_was_checked() -> None:
    digest = _image_digest(_speaches_service())
    assert digest in KOKORO_ORDER_CHECKED, (
        f"The speaches image moved to {digest}, and nobody has checked which "
        "device Kokoro lands on in it. Run the recipe in "
        "docs/operations/speaches-kokoro-gpu.md inside the new image. If it "
        "still builds Kokoro's providers from a set, record the seed that puts "
        "CUDAExecutionProvider first. If it sorts them (speaches >= v0.8.2), "
        "record None and drop PYTHONHASHSEED from the compose file. Then add "
        "the digest to KOKORO_ORDER_CHECKED."
    )


def test_a_set_ordered_image_pins_the_seed_checked_for_it() -> None:
    service = _speaches_service()
    digest = _image_digest(service)
    if digest not in KOKORO_ORDER_CHECKED:
        pytest.skip("unchecked digest; the test above fails for it")
    checked_seed = KOKORO_ORDER_CHECKED[digest]
    if checked_seed is None:
        pytest.skip("this image sorts onnxruntime providers itself; no seed needed")
    pinned = _environment(service).get("PYTHONHASHSEED")
    assert pinned == checked_seed, (
        f"{SERVICE} sets PYTHONHASHSEED={pinned!r}, but {checked_seed!r} is the "
        "seed checked to start Kokoro on CUDA in this image. Without it, each "
        "container start picks CPU or GPU at random and keeps the choice until "
        "the next restart (84 chars/s against ~1,000)."
    )


def test_without_a_pinned_seed_the_order_is_a_coin_flip() -> None:
    """The failure the pin exists for: different seeds, different devices."""
    firsts = {_v081_provider_order(str(seed))[0] for seed in PROBE_SEEDS}
    assert firsts == {"CUDAExecutionProvider", "CPUExecutionProvider"}, (
        f"Seeds {PROBE_SEEDS.start}-{PROBE_SEEDS.stop - 1} put {sorted(firsts)} "
        "first. If one order now wins for every seed, this interpreter no longer "
        "hashes like the image's. Re-check inside the image before trusting the "
        "next test."
    )


@pytest.mark.parametrize(
    "digest,seed",
    [(d, s) for d, s in KOKORO_ORDER_CHECKED.items() if s is not None],
)
def test_the_checked_seed_really_puts_cuda_first(digest: str, seed: str) -> None:
    """Derived, not trusted: rerun speaches' expression under the recorded seed."""
    order = _v081_provider_order(seed)
    assert order == ["CUDAExecutionProvider", "CPUExecutionProvider"], (
        f"PYTHONHASHSEED={seed} (recorded for {digest[:19]}...) gives {order} "
        "under this interpreter, which would start Kokoro on the CPU. Either the "
        "recorded seed is wrong, or this Python hashes strings differently from "
        "the image's. Settle it inside the image with the recipe in "
        "docs/operations/speaches-kokoro-gpu.md."
    )


def _mount_targets(service: dict) -> list[str]:
    """Container-side paths of the service's volumes, short or long syntax.

    Short syntax is ``SRC:DST[:MODE]``, and SRC can itself hold a colon
    (``${USERPROFILE:-${HOME}}/...``), so the target is the last segment that
    is an absolute path, not a fixed index.
    """
    targets: list[str] = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            targets.append(str(volume.get("target") or ""))
            continue
        absolute = [part for part in str(volume).split(":") if part.startswith("/")]
        if absolute:
            targets.append(absolute[-1])
    return [t.rstrip("/") for t in targets if t]


def test_the_cuda_jit_cache_outlives_the_container() -> None:
    """The kernels compiled for the 5090 must survive a recreate.

    Kokoro on the GPU needs CUDA to JIT-compile kernels the image's libraries
    don't ship for sm_120. In a fresh container that made the first read take
    75 s for 506 characters (0.5 s once warm). A cache inside the container is
    thrown away by every recreate, so it has to sit on a bind mount.
    """
    service = _speaches_service()
    cache = _environment(service).get("CUDA_CACHE_PATH", "").rstrip("/")
    targets = _mount_targets(service)
    assert cache and any(cache == target or cache.startswith(target + "/") for target in targets), (
        f"{SERVICE} keeps CUDA's JIT cache at {cache or '~/.nv/ComputeCache (unset)'}, "
        f"which is not under any of its mounts {targets}. Every recreate would "
        "throw the compiled kernels away and the next Kokoro or Whisper call "
        "would pay the compile again (75 s for Kokoro on the 5090)."
    )
