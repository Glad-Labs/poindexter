"""Every sibling module a COPY'd Python file imports must itself be COPY'd.

Earned 2026-09-13: #3730 added ``scripts/tts_sidecars/_voice_paths.py`` next to
``chatterbox_server.py`` and imported it. ``Dockerfile.chatterbox`` copies its
files BY NAME, no CI job builds the CUDA sidecars, and the deploy sync rebuilds
them automatically on any ``scripts/`` change — so the image died on
``ModuleNotFoundError`` at import, restarted 507 times, and paged the operator
critical for eight hours through a downstream probe.

What it checks, for every Dockerfile a compose file builds (and every
``scripts/Dockerfile.*``):

* parse each ``COPY`` (flags, globs, directories, JSON form; ``--from=`` stage
  copies are skipped — they are not source);
* expand the sources against the service's build context to the set of
  ``host file -> image path`` pairs;
* for each copied ``.py`` file, AST-walk its imports; any import that resolves
  to a SIBLING module or package on the host (``<dir>/<name>.py`` or
  ``<dir>/<name>/__init__.py``) must be copied too, and must land in the same
  image directory, or the import fails inside the container.

It does not resolve package imports that ride PYTHONPATH (``poindexter.brain``
from a script two directories up) — the brain-import-isolation lint owns the
brain's copy set, and the auto-embed image copies the whole tree. Escape hatch:
``# image-copy-ok`` on the import line (an import that is guarded and truly
optional inside the image).

Stdlib only; ~1 s. Floors: fails when there is no ``scripts/`` dir or when it
examined zero copied Python files (a lint that scanned nothing has not passed).
"""

from __future__ import annotations

import ast
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

LINT = "dockerfile_copy_closure_lint"
REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.local.yml", "docker-compose.consumer.yml")
ESCAPE_MARKER = "# image-copy-ok"
_GLOB_CHARS = set("*?[")


@dataclass
class CopyStep:
    sources: list[str]
    dest: str
    lineno: int
    from_stage: str | None = None


@dataclass
class ImageCopies:
    dockerfile: Path
    context: Path
    files: dict[Path, str] = field(default_factory=dict)  # host file -> image path


# ---------------------------------------------------------------------------
# Compose: which Dockerfiles are built, with which context
# ---------------------------------------------------------------------------

def compose_build_contexts(repo_root: Path) -> dict[Path, Path]:
    """``{dockerfile (resolved) : build context (resolved)}`` from the compose files.

    Regex-parsed on purpose (no PyYAML in the lint's environment): a service
    is a two-space-indented ``name:`` header; its ``build:`` block carries
    ``context:`` and ``dockerfile:``. A ``build: ./dir`` shorthand means
    ``dir/Dockerfile``.
    """
    found: dict[Path, Path] = {}
    for name in COMPOSE_FILES:
        path = repo_root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        headers = list(re.finditer(r"^  ([A-Za-z0-9][A-Za-z0-9._-]*):\s*$", text, re.M))
        for i, m in enumerate(headers):
            body = text[m.end(): headers[i + 1].start() if i + 1 < len(headers) else len(text)]
            short = re.search(r"^\s*build:\s*(\S+)\s*$", body, re.M)
            ctx = re.search(r"^\s*context:\s*(\S+)", body, re.M)
            dfile = re.search(r"^\s*dockerfile:\s*(\S+)", body, re.M)
            if short and not ctx:
                context = (repo_root / short.group(1)).resolve()
                found[(context / "Dockerfile").resolve()] = context
            elif ctx:
                context = (repo_root / ctx.group(1)).resolve()
                dockerfile = (context / (dfile.group(1) if dfile else "Dockerfile")).resolve()
                found[dockerfile] = context
    return found


# ---------------------------------------------------------------------------
# Dockerfile: COPY parsing + source expansion
# ---------------------------------------------------------------------------

def parse_copy_steps(text: str) -> list[CopyStep]:
    steps: list[CopyStep] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        start = i + 1
        # join continuation lines
        while raw.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            raw = raw.rstrip()[:-1] + " " + lines[i]
        i += 1
        stripped = raw.strip()
        if not re.match(r"(?i)^(COPY|ADD)\b", stripped):
            continue
        rest = stripped.split(None, 1)[1] if " " in stripped else ""
        rest = rest.split(" #", 1)[0].strip()
        from_stage = None
        json_form = re.match(r"^\[(.*)\]$", rest)
        if json_form:
            parts = [p.strip().strip('"') for p in json_form.group(1).split(",") if p.strip()]
        else:
            try:
                parts = shlex.split(rest)
            except ValueError:
                parts = rest.split()
        args: list[str] = []
        for p in parts:
            if p.startswith("--"):
                if p.startswith("--from="):
                    from_stage = p.split("=", 1)[1]
                continue
            args.append(p)
        if len(args) < 2:
            continue
        steps.append(CopyStep(sources=args[:-1], dest=args[-1], lineno=start, from_stage=from_stage))
    return steps


def _has_glob(pattern: str) -> bool:
    return any(ch in _GLOB_CHARS for ch in pattern)


def expand_copies(context: Path, steps: list[CopyStep]) -> dict[Path, str]:
    """Resolve every (non-stage) COPY into ``host file -> image path``."""
    out: dict[Path, str] = {}
    for step in steps:
        if step.from_stage is not None:
            continue
        multi = len(step.sources) > 1 or step.dest.endswith("/") or any(_has_glob(s) for s in step.sources)
        for src in step.sources:
            src_norm = src.lstrip("./") or "."
            matches: list[Path]
            if _has_glob(src_norm):
                matches = sorted(p for p in context.glob(src_norm) if p.is_file() or p.is_dir())
            else:
                candidate = (context / src_norm)
                matches = [candidate] if candidate.exists() else []
            for m in matches:
                if m.is_dir():
                    base = step.dest.rstrip("/")
                    if multi or src_norm.endswith("/") or src_norm == ".":
                        base = step.dest.rstrip("/") if src_norm != "." else step.dest.rstrip("/") or "/"
                    for f in sorted(m.rglob("*")):
                        if f.is_file():
                            rel = f.relative_to(m).as_posix()
                            out.setdefault(f.resolve(), f"{base}/{rel}" if base else rel)
                else:
                    if multi:
                        image = f"{step.dest.rstrip('/')}/{m.name}"
                    else:
                        image = step.dest
                    out.setdefault(m.resolve(), image)
    return out


# ---------------------------------------------------------------------------
# Python: sibling imports of each copied module
# ---------------------------------------------------------------------------

def sibling_imports(py_file: Path) -> list[tuple[str, int]]:
    """``(name, lineno)`` for imports that resolve to a sibling module/package on the host."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return []
    source_lines = py_file.read_text(encoding="utf-8").splitlines()
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                if node.module:
                    names.append((node.module.split(".")[0], node.lineno))
                else:
                    names.extend((alias.name, node.lineno) for alias in node.names)
            elif node.module:
                names.append((node.module.split(".")[0], node.lineno))
    out: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for name, lineno in names:
        line_text = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
        if ESCAPE_MARKER in line_text:
            continue
        if (py_file.parent / f"{name}.py").is_file() or (py_file.parent / name / "__init__.py").is_file():
            if (name, lineno) not in seen:
                seen.add((name, lineno))
                out.append((name, lineno))
    return out


def check_image(copies: ImageCopies, repo_root: Path) -> tuple[list[str], int]:
    """Return (offences, examined_python_files) for one image."""
    offences: list[str] = []
    examined = 0
    by_host = copies.files
    for host, image_path in sorted(by_host.items()):
        if host.suffix != ".py":
            continue
        examined += 1
        image_dir = image_path.rsplit("/", 1)[0] if "/" in image_path else ""
        # NB: try/except, not Path.is_relative_to — that is 3.9+, and this
        # script is pinned to the runner's SYSTEM interpreter (see the
        # workflow step comment), which on the self-hosted runner is 3.8.10.
        try:
            rel_py = host.relative_to(repo_root).as_posix()
        except ValueError:
            rel_py = str(host)
        rel_df = copies.dockerfile.relative_to(repo_root).as_posix()
        for name, lineno in sibling_imports(host):
            module = host.parent / f"{name}.py"
            package = host.parent / name / "__init__.py"
            target = module if module.is_file() else package
            if target.resolve() not in by_host:
                offences.append(
                    f"{rel_py}:{lineno}: imports sibling `{name}` ({target.relative_to(repo_root).as_posix()}) "
                    f"but {rel_df} never COPYs it — the import fails inside the image"
                )
                continue
            sib_image = by_host[target.resolve()]
            sib_dir = sib_image.rsplit("/", 1)[0] if "/" in sib_image else ""
            if target is package:
                sib_dir = sib_dir.rsplit("/", 1)[0] if "/" in sib_dir else ""
            if sib_dir != image_dir:
                offences.append(
                    f"{rel_py}:{lineno}: imports sibling `{name}`, copied to {sib_image} while this module "
                    f"lands in {image_dir or '/'} ({rel_df}) — not importable from there"
                )
    return offences, examined


def collect_images(repo_root: Path) -> list[ImageCopies]:
    contexts = compose_build_contexts(repo_root)
    scripts_dir = repo_root / "scripts"
    for df in sorted(scripts_dir.glob("Dockerfile.*")):
        contexts.setdefault(df.resolve(), repo_root.resolve())  # loose sidecars default to the repo root
    images: list[ImageCopies] = []
    for dockerfile, context in sorted(contexts.items()):
        if not dockerfile.is_file():
            continue
        steps = parse_copy_steps(dockerfile.read_text(encoding="utf-8", errors="replace"))
        images.append(ImageCopies(dockerfile=dockerfile, context=context, files=expand_copies(context, steps)))
    return images


def main() -> int:
    require_dir(REPO_ROOT / "scripts", lint=LINT)
    offences: list[str] = []
    examined = 0
    images = collect_images(REPO_ROOT)
    for img in images:
        offs, n = check_image(img, REPO_ROOT)
        offences.extend(offs)
        examined += n
    require_scanned(examined, lint=LINT, what="copied python files", roots=(REPO_ROOT / "scripts",))
    if offences:
        print("\n".join(offences))
        print(
            f"\n{LINT}: {len(offences)} sibling import(s) missing from their image across "
            f"{len(images)} Dockerfiles ({examined} copied python files). Add the COPY line "
            f"(or use a glob copy for the whole sidecar dir); `{ESCAPE_MARKER}` marks a guarded optional import."
        )
        return 1
    print(f"{LINT}: OK — {examined} copied python files across {len(images)} Dockerfiles import only what their image carries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
