"""Sanitise prompt-model replies before they reach a diffusion model.

Shared by the featured-image stage (``modules/content/stages/
source_featured_image.py``) and the inline-image atom helper
(``modules/content/atoms/_image_helpers.py``). It lives in ``services/``
because an atom may not import a stage — see the atom-independence rule in
CLAUDE.md and ``scripts/ci/atom_independence_lint.py``.

Both paths ask a local instruct model for one SDXL prompt and both were
passing whatever came back straight to the renderer. See
:func:`clean_image_prompt` for what actually came back.
"""
from __future__ import annotations

# Labels the prompt-writing model echoes back when it plans out loud instead of
# answering. Matched at the START of a line (after bullet/indent stripping), so
# a prompt that merely mentions "style" in prose is untouched.
_SCAFFOLD_LABELS = (
    "subject:", "art style:", "style:", "style keywords:", "constraints:",
    "constraint:", "requirement:", "requirements:", "output:", "output format:",
    "format:", "core theme:", "concept:", "visuals:", "notes:", "note:",
)
_BULLET_PREFIXES = ("*", "-", "•", "–", "+")
# Below this a "prompt" is a fragment, not a scene worth rendering.
_MIN_USABLE_PROMPT_CHARS = 25


def strip_bullet(line: str) -> tuple[str, bool]:
    """Return ``(text, was_bulleted)`` for one reply line.

    Bullet-ness is the primary scaffolding signal, not the labels: the brief
    asks for "1-2 sentences, nothing else", so a model that answers in a
    markdown LIST is enumerating the requirements rather than writing a prompt.
    Label matching alone let the unlabelled tail bullets ("Concrete/specific
    subject or visual metaphor.", "No generic glowing circuit boards.") ride
    through into the render.
    """
    stripped = line.strip()
    for bullet in _BULLET_PREFIXES:
        if stripped.startswith(bullet):
            return stripped[len(bullet):].strip(), True
    return stripped, False


def clean_image_prompt(raw: str) -> str:
    """Reduce a prompt-model reply to the image prompt, or "" if there isn't one.

    Instruction-tuned local models frequently answer the ``image.featured_image``
    skill by *restating the brief* — a markdown plan of ``Subject:`` /
    ``Constraints:`` / ``Output ONLY the prompt`` bullets — and
    ``image_prompt_max_tokens`` then truncates them before they ever reach the
    actual prompt. That reply was passed to SDXL verbatim: 26 of the 39 posts
    published in the 60 days before poindexter#3229 carry a bullet-list
    scratchpad in ``posts.featured_image_data->>'image_gen_prompt'``, so the
    diffusion model was being asked to illustrate the word "Constraints".

    Two salvage paths, because the leak takes two shapes in the wild:

    * prose first, scaffolding trailing — keep the head, drop from the first
      scaffolding line on;
    * scaffolding only — the ``Subject:`` bullet still describes a real scene,
      so lift its value and use that.

    Returns "" when neither yields something renderable; the caller then falls
    back to a prompt that still names the subject.
    """
    text = (raw or "").strip().strip("`").strip()
    if not text:
        return ""

    head: list[str] = []
    subject_salvage = ""
    for line in text.splitlines():
        body, bulleted = strip_bullet(line)
        lowered = body.lower()
        label = next((lbl for lbl in _SCAFFOLD_LABELS if lowered.startswith(lbl)), None)
        scaffolding = bulleted or label is not None or lowered.startswith("output only")
        if not scaffolding:
            head.append(body)
            continue
        # First scaffolding line ends the usable prose, but keep scanning for a
        # Subject: value in case the head turns out to be empty.
        if label in ("subject:", "concept:") and not subject_salvage:
            subject_salvage = body[len(label):].strip()
        if head and " ".join(head).strip():
            break

    candidate = " ".join(part for part in head if part).strip()
    if len(candidate) < _MIN_USABLE_PROMPT_CHARS:
        candidate = subject_salvage.strip()
    # A leading "Prompt:"/"Image prompt:" label is the model narrating its
    # answer rather than scaffolding — strip the label, keep the sentence.
    for lead in ("image prompt:", "prompt:"):
        if candidate.lower().startswith(lead):
            candidate = candidate[len(lead):].strip()
    candidate = candidate.strip().strip('"').strip()
    return candidate if len(candidate) >= _MIN_USABLE_PROMPT_CHARS else ""


def subject_fallback_prompt(subject: str, style: str, style_tags: str) -> str:
    """Deterministic prompt used when the LLM gives us nothing usable.

    Keeps the SUBJECT. The previous fallback was
    ``f"{style}, {style_tags}, no text"`` — style only — so every
    degraded run produced a handsome image of nothing in particular. An
    off-topic featured image is its own defect (it has to relate to the post),
    independent of how it was produced, so a fallback that drops the subject
    trades a visible failure for an invisible one.
    """
    subject = " ".join((subject or "").split())[:300].strip().rstrip(".")
    parts = [p for p in (subject, style, style_tags) if p]
    return ", ".join(parts) + ", no text"
