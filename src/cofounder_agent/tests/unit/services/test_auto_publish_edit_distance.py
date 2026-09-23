"""What the auto-publish gate's edit distance actually measures.

Found 2026-09-23 by recomputing the metric over real approvals:
``difflib.SequenceMatcher`` discards every element occurring in more than 1%
of a sequence longer than 200 — across an article that is every common
letter — so the derived distance inflated. A genuine ~640-character edit was
recorded as **10,354**, and a scattered proofreading pass worth ~20
characters scores 7,213.

This did NOT change gate outcomes at ``max_edit_distance=50``: an untouched
post scored 0 either way and a genuinely edited one cleared 50 either way, so
the trust window has been filling correctly and dev_diary has been
auto-publishing on it. What was broken was the NUMBER — the ramp's primary
trust signal could not be read, so no threshold could be tuned from it.

Corrected, the distribution is bimodal: a post is either untouched or
substantially edited, which is why 50 needed no change.

NOT a fault, checked and ruled out: the publisher's ``extract_title_from_content``
heading strip. ``publish_service`` passes ``draft_content`` as the
post-approve side, so both sides are pre-strip and the heading never enters
the diff.
"""

from __future__ import annotations

import difflib

from poindexter.modules.content import auto_publish_gate as gate

# Long enough to clear difflib's 200-element autojunk floor, and VARIED so the
# character matcher cannot lock onto one huge identical block and mask the
# collapse (a repetitive fixture scores far higher than real prose does).
_VOCAB = (
    "distributed reinforcement learning keeps policy weights synchronized across "
    "separate jobs without a high speed interconnect the adapter becomes the "
    "transport and a storage bucket stands in for a shared network so trainer and "
    "inference replicas agree on which version they serve while drift silently "
    "scores rollouts against an obsolete policy"
).split()
_BODY = " ".join(_VOCAB[(i * 7 + (i * i) % 11) % len(_VOCAB)] for i in range(600))


def _proofread(body: str, at: tuple[int, ...]) -> str:
    """A human proofreading pass: a few small fixes SCATTERED through the
    article. Scatter is what matters — a single localised change leaves the
    character matcher two big blocks to lock onto and inflates barely at all
    (11 vs 9), while three scattered fixes collapse it completely."""
    return " ".join(
        ("the" if i in at and w != "the" else w) for i, w in enumerate(body.split())
    )


class TestAutojunkNoLongerInflates:
    def test_a_proofreading_pass_is_small(self):
        """Three typo fixes in a 4,000-character article: ~20 characters of
        real editing, comfortably inside max_edit_distance=50."""
        assert gate.edit_distance_chars(_BODY, _proofread(_BODY, (60, 300, 540))) < 50

    def test_the_old_comparison_scored_that_same_pass_at_thousands(self):
        """The guard. This is the calculation that shipped, and it must still
        blow past the threshold on ~20 characters of editing, or the fix is no
        longer doing anything. Measured: 7,213 against a true 20 — a 360x
        inflation, and the reason a proofread post could never be a clean
        run."""
        edited = _proofread(_BODY, (60, 300, 540))
        ratio = difflib.SequenceMatcher(a=_BODY, b=edited).ratio()
        shipped = max(abs(len(_BODY) - len(edited)),
                      int(2 * (1 - ratio) * max(len(_BODY), len(edited))))
        assert shipped > 1000, "autojunk no longer collapses the ratio; re-check the fix"
        assert gate.edit_distance_chars(_BODY, edited) < 50

    def test_a_real_edit_is_still_counted(self):
        """The fix must not make everything look clean."""
        edited = _BODY + " " + " ".join(_VOCAB) * 6
        assert gate.edit_distance_chars(_BODY, edited) > 300

    def test_identical_bodies_are_zero(self):
        assert gate.edit_distance_chars(_BODY, _BODY) == 0

    def test_token_granularity_rounds_UP_never_down(self):
        """A one-letter change charges the whole word. Over-counting is the
        safe direction for a gate that decides to publish without a human."""
        edited = _BODY.replace("synchronized", "synchronised", 1)
        assert gate.edit_distance_chars(_BODY, edited) >= 1

    def test_lines_use_the_same_reasoning(self):
        pre = "\n".join(f"line {i} of the draft body text" for i in range(400))
        post = pre.replace("line 200 of", "LINE 200 of", 1)
        assert gate.edit_distance_lines(pre, post) == 1
