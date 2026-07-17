"""Every Hugging Face download in scripts/ pins a revision.

An unpinned `from_pretrained()` / `hf_hub_download()` / `load_lora_weights()`
resolves to whatever the repo's `main` points at *right now*. That is a
supply-chain exposure (the repo owner — or anyone who compromises them — can
swap the weights under a running server) and a reproducibility hole (a model
silently changes between two cold starts of the same commit).

Why this test exists on top of bandit's B615
--------------------------------------------
B615 only knows the `transformers` + `huggingface_hub` APIs. It does **not**
recognise `diffusers` classes, so it flagged exactly 2 of the 10 real call sites
across these three scripts — it saw `UMT5EncoderModel.from_pretrained` and
`hf_hub_download`, and stayed silent on `AutoencoderKLWan`,
`WanTransformer3DModel`, `WanImageToVideoPipeline`, `StableDiffusionXLPipeline`,
`ZImagePipeline`, and `load_lora_weights`. Fixing only what the linter noticed
would have left the live image + video servers unpinned. This test pins the
property the linter is a proxy for, so a new `diffusers` download can't land
unpinned just because bandit can't see it.

AST-based on purpose: importing these scripts drags in torch/diffusers (and the
image-gen-server loader has to stub torch carefully — see
test_image_gen_server_unload.py), which is a lot of machinery for a question the
source text answers directly.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "wan-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/wan-server.py")


# Call names that fetch from the HF hub. `from_config` is deliberately absent —
# it reads an already-local config object and downloads nothing.
_HF_DOWNLOAD_CALLS = frozenset({"from_pretrained", "hf_hub_download", "snapshot_download", "load_lora_weights"})

# The scripts that reach the hub. tts_sidecars/chatterbox_server.py is excluded:
# its `ChatterboxTTS.from_pretrained(device=...)` takes no repo/revision at all
# (the library resolves its own default), so there is nothing to pin without
# upstream support.
_SCRIPTS = ("wan-server.py", "image-gen-server.py", "image_bakeoff.py")


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _hf_download_calls(source: str) -> list[tuple[int, str, bool]]:
    """Return (lineno, call_name, has_revision) for every call that actually
    reaches the hub.

    `load_lora_weights` is overloaded: given a repo id it downloads (and needs a
    pin), but given a local path — e.g. `load_lora_weights(hf_hub_download(...))`
    — it just reads a file off disk, and the pin belongs on the inner download
    instead. So skip it when its first argument is itself a call; that inner
    call is checked on its own line.
    """
    tree = ast.parse(source)
    out: list[tuple[int, str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in _HF_DOWNLOAD_CALLS:
            continue
        if name == "load_lora_weights" and node.args and isinstance(node.args[0], ast.Call):
            continue  # fed a local path from a nested (separately-pinned) download
        kwargs = {k.arg for k in node.keywords if k.arg}
        # `**kwargs` (arg is None) could smuggle a revision in, but relying on
        # that is exactly the ambiguity we don't want — require it explicit.
        has_revision = "revision" in kwargs
        out.append((node.lineno, name, has_revision))
    return out


def _dataclass_field_order(tree: ast.Module, name: str) -> list[str]:
    """Field names of dataclass `name`, in declaration order."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            fields = [
                f.target.id for f in node.body if isinstance(f, ast.AnnAssign) and isinstance(f.target, ast.Name)
            ]
            assert fields, f"{name} declares no annotated fields"
            return fields
    raise AssertionError(f"could not find dataclass {name}")


def _bind_args(node: ast.Call, fields: list[str]) -> dict[str, ast.expr]:
    """Map a constructor call's arguments — positional *and* keyword — onto field
    names, the way Python itself would.

    Reading only `node.keywords` would miss the bakeoff ROSTER entirely: it
    passes `revision` as the 3rd positional arg, so a keyword-only check would
    call every pinned entry unpinned.
    """
    assert not any(isinstance(a, ast.Starred) for a in node.args), (
        f"{fields[0] if fields else '?'}: *args unpacking defeats positional binding; "
        "pass fields explicitly so this test can see them"
    )
    assert len(node.args) <= len(fields), "more positional args than declared fields — is the field list stale?"
    bound: dict[str, ast.expr] = dict(zip(fields, node.args, strict=False))
    bound.update({k.arg: k.value for k in node.keywords if k.arg})
    return bound


@pytest.mark.parametrize("script", _SCRIPTS)
def test_every_hf_download_pins_a_revision(script: str):
    path = _repo_root() / "scripts" / script
    calls = _hf_download_calls(path.read_text(encoding="utf-8"))
    assert calls, f"{script}: expected at least one HF download call; did the loader move?"
    unpinned = [(ln, name) for ln, name, pinned in calls if not pinned]
    assert not unpinned, (
        f"{script} has HF download(s) with no explicit `revision=`:\n"
        + "\n".join(f"  L{ln}: {name}(...)" for ln, name in unpinned)
        + "\n\nPin it to a commit SHA. bandit's B615 does NOT cover diffusers "
        "classes, so a green bandit run is not evidence this is safe."
    )


def test_registry_and_roster_entries_declare_a_revision():
    """Every declared model config carries a revision SHA.

    Guards the data rather than the call site: a new model added to
    image-gen-server's REGISTRY or the bakeoff ROSTER must bring its own pin,
    otherwise it would thread `revision=None` through an otherwise-pinned call
    and silently ride `main`.
    """
    root = _repo_root()
    checked = 0
    for script, ctor in (("image-gen-server.py", "ModelConfig"), ("image_bakeoff.py", "BakeoffModel")):
        source = (root / "scripts" / script).read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Field order from the dataclass itself, so positional args resolve by
        # name. The bakeoff ROSTER passes most fields positionally, so a
        # keyword-only read would miss every pin it declares.
        fields = _dataclass_field_order(tree, ctor)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == ctor):
                continue
            bound = _bind_args(node, fields)
            label = getattr(bound.get("model_id") or bound.get("key"), "value", "?")
            rev = bound.get("revision")
            assert rev is not None, f"{script}: {ctor}({label!r}) declares no revision"
            assert isinstance(rev, ast.Constant) and isinstance(rev.value, str) and len(rev.value) == 40, (
                f"{script}: {ctor}({label!r}) revision must be a full 40-char commit SHA"
            )
            # A LoRA is a second, independent repo — it needs its own pin.
            if bound.get("lora_repo") is not None:
                assert bound.get("lora_revision") is not None, (
                    f"{script}: {ctor}({label!r}) sets lora_repo but no lora_revision"
                )
            checked += 1
    assert checked >= 16, f"expected to check every REGISTRY+ROSTER entry, only saw {checked}"


def _wan_resolution_source() -> str:
    """Lift wan-server's model+revision resolution block out of the module.

    wan-server.py imports torch/diffusers at module scope, so the test can't
    import it. Lifting the real statements and exec'ing them still exercises the
    production source text, which a substring assertion does not: `"X" in src`
    stays true when X survives only in a comment.
    """
    src = (_repo_root() / "scripts" / "wan-server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"_DEFAULT_MODEL_ID", "MODEL_ID", "_DEFAULT_MODEL_REVISION", "MODEL_REVISION"}
    lifted: list[str] = []
    for stmt in tree.body:
        assigned: set[str] = set()
        if isinstance(stmt, ast.Assign | ast.AnnAssign | ast.If):
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Assign):
                    assigned |= {t.id for t in sub.targets if isinstance(t, ast.Name)}
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    assigned.add(sub.target.id)
        if assigned & wanted:
            segment = ast.get_source_segment(src, stmt)
            assert segment is not None
            lifted.append(segment)
    assert len(lifted) >= 4, f"expected wan-server's 4-statement resolution block, lifted {len(lifted)}"
    return "\n".join(lifted)


def _resolve_wan(env: dict[str, str]) -> dict[str, object]:
    """Run the lifted block against a fake environment."""
    ns: dict[str, object] = {"os": SimpleNamespace(getenv=lambda k, d=None: env.get(k, d))}
    exec(compile(_wan_resolution_source(), "<wan-server:resolution>", "exec"), ns)  # noqa: S102
    return ns


def test_wan_server_pins_its_default_model():
    ns = _resolve_wan({})
    assert ns["MODEL_REVISION"] == ns["_DEFAULT_MODEL_REVISION"]
    rev = ns["MODEL_REVISION"]
    assert isinstance(rev, str) and len(rev) == 40, f"default pin must be a full commit SHA, got {rev!r}"


def test_wan_server_does_not_force_the_default_sha_onto_a_custom_model():
    """The default SHA is a commit in the *default* repo. Applying it to an
    operator's own WAN_MODEL_ID would request a commit that doesn't exist there
    — a guaranteed hard failure at load. Unpinned-but-working beats broken, and
    the server warns instead (see the logger.warning on MODEL_REVISION is None).
    """
    ns = _resolve_wan({"WAN_MODEL_ID": "someone-else/custom-wan"})
    assert ns["MODEL_REVISION"] is None


def test_wan_server_uses_an_operator_supplied_revision():
    ns = _resolve_wan({"WAN_MODEL_ID": "someone-else/custom-wan", "WAN_MODEL_REVISION": "a" * 40})
    assert ns["MODEL_REVISION"] == "a" * 40


def test_wan_server_treats_a_blank_revision_env_as_unset():
    """`WAN_MODEL_REVISION=` in a compose file must not pin the empty string."""
    ns = _resolve_wan({"WAN_MODEL_REVISION": "   "})
    assert ns["MODEL_REVISION"] == ns["_DEFAULT_MODEL_REVISION"]
