"""Contract tests for ``services.llm_providers.thinking_models``.

Pins:
- ``is_thinking_model`` returns True for canonical thinking-model
  identifiers in any common form (bare name, ``ollama/`` prefix,
  uppercased) and False for everything else.
- ``resolve_thinking_substrings`` reads from ``site_config`` when
  available, falls back to the hardcoded list on missing key /
  malformed JSON / non-list / empty list / no site_config.
- The helpers compose: an operator who edits the JSON to add a new
  family gets immediate recognition without a code change.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.llm_providers.thinking_models import (
    _DEFAULT_SUBSTRINGS,
    is_thinking_model,
    resolve_thinking_substrings,
    strip_reasoning_artifacts,
    strip_think_blocks,
)

# ---- strip_think_blocks (added 2026-05-26 to defend brain triage) ----------


def test_strip_think_blocks_removes_single_block():
    """The captured shape — model emits a reasoning trace inside
    ``<think>...</think>`` then its actual prose. The brain only wants
    the prose."""
    raw = (
        "<think>Reasoning about the alert... let me check cost_logs.</think>"
        "Ollama looks healthy; the staleness is downstream from a stuck flow."
    )
    out = strip_think_blocks(raw)
    assert out == "Ollama looks healthy; the staleness is downstream from a stuck flow."


def test_strip_think_blocks_handles_multiline():
    """Reasoning traces span newlines; the DOTALL flag must include them."""
    raw = "<think>\nstep 1\nstep 2\nstep 3\n</think>\nFinal diagnosis here."
    out = strip_think_blocks(raw)
    assert out == "Final diagnosis here."


def test_strip_think_blocks_removes_multiple_blocks():
    """Some thinking models emit multiple ``<think>`` blocks interleaved
    with prose. Each block is removed independently."""
    raw = (
        "<think>part A</think>"
        "First sentence."
        "<think>part B</think>"
        " Second sentence."
        "<think>part C</think>"
    )
    out = strip_think_blocks(raw)
    assert out == "First sentence. Second sentence."


def test_strip_think_blocks_case_insensitive():
    """Some providers emit ``<Think>`` or ``<THINK>``. The strip must
    catch every casing."""
    raw = "<Think>cap-T</Think><THINK>all-caps</THINK>Real text."
    out = strip_think_blocks(raw)
    assert out == "Real text."


def test_strip_think_blocks_returns_empty_when_all_inside_tags():
    """The 2026-05-26 captured failure: the thinking model burned every
    token on reasoning. After stripping the whole response is empty."""
    raw = "<think>I'm thinking but never finished my answer because num_predict ran out.</think>"
    out = strip_think_blocks(raw)
    assert out == ""


def test_strip_think_blocks_handles_no_tags():
    """Most calls won't have think tags — the strip must be idempotent
    on plain text."""
    raw = "Plain diagnosis with no thinking tags."
    out = strip_think_blocks(raw)
    assert out == raw


def test_strip_think_blocks_on_empty_returns_empty():
    assert strip_think_blocks("") == ""
    assert strip_think_blocks(None) == ""  # type: ignore[arg-type]


def test_strip_think_blocks_trims_outer_whitespace():
    """Stripped responses often have leading/trailing whitespace where
    the think tags used to sit; the helper trims so callers don't have
    to do it again."""
    raw = "  <think>noise</think>\n\nDiagnosis.  \n"
    out = strip_think_blocks(raw)
    assert out == "Diagnosis."


# ---- strip_reasoning_artifacts (added 2026-06-09 after the channel-leak) ---
#
# Two real prod captures (2026-06-09): a mis-templated gemma import and
# glm-4.7-5090 both emitted article bodies beginning at char 1 with a
# mangled-Harmony channel header "<|channel>thought\n<channel|>..." — the
# whole article living inside the "thought" channel. strip_think_blocks
# (which only knows <think>…</think>) missed it entirely.


class TestStripReasoningArtifacts:
    def test_real_gemma_channel_leak(self) -> None:
        """The exact prod capture from task b8b227c6 (gemma-4-31B-it-qat)."""
        raw = "<|channel>thought\n<channel|>The release of Claude Fable 5 marks a pivot."
        assert strip_reasoning_artifacts(raw) == "The release of Claude Fable 5 marks a pivot."

    def test_real_glm_channel_leak_with_heading(self) -> None:
        """Prod capture from task a94229fa (glm-4.7-5090) — body is a heading."""
        raw = "<|channel>thought\n<channel|># HTML5 Deep Dive\n\nFor indie devs."
        assert strip_reasoning_artifacts(raw) == "# HTML5 Deep Dive\n\nFor indie devs."

    def test_clean_markdown_is_noop(self) -> None:
        clean = "# Title\n\nSome **bold** prose with a [link](/posts/x)."
        assert strip_reasoning_artifacts(clean) == clean

    def test_semantic_html_preserved(self) -> None:
        """The allowlist is keyword-exact — never a generic <|...|> sweep — so
        legitimate semantic HTML the writer emits survives."""
        html = "<section>\n<article>Hello world</article>\n</section>"
        assert strip_reasoning_artifacts(html) == html

    def test_fenced_control_token_is_an_example_kept(self) -> None:
        """An AI/ML post explaining the Harmony format shows the token in a
        code fence — fence-aware stripping must leave it intact."""
        fenced = "The Harmony format:\n\n```\n<|channel|>analysis<|message|>hi\n```\n\nDone."
        assert strip_reasoning_artifacts(fenced) == fenced

    def test_inline_code_token_preserved(self) -> None:
        inline = "Use `<|im_start|>` to begin a turn."
        assert strip_reasoning_artifacts(inline) == inline

    def test_think_block_dropped_when_answer_follows(self) -> None:
        raw = "<think>let me reason about this</think>\n\nThe answer is 42."
        assert strip_reasoning_artifacts(raw) == "The answer is 42."

    def test_think_block_salvaged_when_it_is_the_answer(self) -> None:
        """Some reasoning models put the *output* inside the think block —
        unwrap rather than return empty."""
        raw = "<think>The answer is 42.</think>"
        assert strip_reasoning_artifacts(raw) == "The answer is 42."

    def test_standalone_marker_removed_prose_kept(self) -> None:
        out = strip_reasoning_artifacts("Hello <|im_end|> world")
        assert "<|im_end|>" not in out
        assert "Hello" in out and "world" in out

    def test_broken_gemma_turn_header_form(self) -> None:
        """The broken gemma Modelfile used <|turn>role markers; strip the
        marker and its role label."""
        raw = "<|turn>model\nContent body here<turn|>"
        assert strip_reasoning_artifacts(raw) == "Content body here"

    def test_leak_stripped_but_later_fenced_example_kept(self) -> None:
        """Mixed: a real leak prefix AND a legit fenced example in one body."""
        raw = "<|channel>thought\n<channel|>Intro.\n\n```\n<|channel|>x\n```\n\nEnd."
        assert strip_reasoning_artifacts(raw) == "Intro.\n\n```\n<|channel|>x\n```\n\nEnd."

    def test_fast_paths(self) -> None:
        assert strip_reasoning_artifacts("") == ""
        assert strip_reasoning_artifacts(None) is None  # type: ignore[arg-type]
        assert strip_reasoning_artifacts("plain text") == "plain text"

    def test_idempotent(self) -> None:
        once = strip_reasoning_artifacts("<|channel>thought\n<channel|>The release.")
        assert strip_reasoning_artifacts(once) == once

    # --- regression cases from the 2026-06-09 adversarial diff review --------

    @pytest.mark.parametrize(
        "prose",
        [
            "Set the <user> field to your handle.",
            "Use a <message> queue like RabbitMQ.",
            "The function takes <start> and <end> arguments.",
            "Define a <System> component and a <User> avatar",
        ],
    )
    def test_bareword_tokens_survive(self, prose: str) -> None:
        """A control keyword with NO pipe is legitimate JSX / HTML / prose (the
        brand niches are AI/ML, gaming, PC hardware) — a pipe is required for a
        match, so these are never stripped."""
        assert strip_reasoning_artifacts(prose) == prose

    def test_think_block_wrapping_a_code_fence_is_stripped(self) -> None:
        """The <think> pass is global, so a reasoning block that *wraps* a code
        fence is still matched as one balanced pair."""
        raw = "<think>Reasoning before code:\n```\nx=1\n```\nand after</think>\n\nFinal answer here."
        assert strip_reasoning_artifacts(raw) == "Final answer here."

    def test_reasoning_then_fenced_answer_keeps_only_the_code(self) -> None:
        """unwrap-vs-drop is decided globally: a real answer (the code fence)
        survives outside the block, so the reasoning is dropped — not unwrapped
        and published."""
        raw = "<think>Let me write the function now.</think>\n\n```python\ndef f(): return 1\n```"
        assert strip_reasoning_artifacts(raw) == "```python\ndef f(): return 1\n```"

    def test_proper_multichannel_harmony_keeps_only_final(self) -> None:
        """Proper multi-channel Harmony: the analysis/commentary channel is
        private reasoning; keep only the final-channel body."""
        raw = (
            "<|channel|>analysis<|message|>I should lie about benchmarks."
            "<|channel|>final<|message|>Real answer."
        )
        assert strip_reasoning_artifacts(raw) == "Real answer."

    def test_true_noop_with_stray_angle_bracket_and_blank_lines(self) -> None:
        """When nothing matched, the input is returned UNCHANGED — blank-line
        normalization must not fire just because a non-token '<' is present."""
        raw = "# T\n\n<br>\n\n\n\nNext"
        assert strip_reasoning_artifacts(raw) == raw

    def test_piped_token_in_unfenced_prose_is_stripped(self) -> None:
        """Accepted tradeoff: a pipe-bearing control token in *unfenced* prose
        is treated as a leak and removed (writers show such tokens in code
        fences / inline code, which are preserved)."""
        assert "<|im_start|>" not in strip_reasoning_artifacts("The model uses <|im_start|> markers.")

    @pytest.mark.parametrize(
        "payload",
        [
            '{"explanation": "The <|im_start|> token starts a turn", "score": 5}',
            '{"note": "use <|channel|>final<|message|> for the answer"}',
            '[{"a": "<|user|>"}]',
        ],
    )
    def test_caller_owned_valid_json_is_not_mutated(self, payload: str) -> None:
        """A payload that already parses as JSON is the caller's (e.g. a
        pipeline_architect graph spec consumed via json.loads). A control-token
        literal inside a string value must NOT be stripped/sliced — that would
        be silent JSON corruption."""
        assert strip_reasoning_artifacts(payload) == payload

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('<think>build it</think>\n{"nodes": [1, 2]}', '{"nodes": [1, 2]}'),
            ('<|channel>thought\n<channel|>{"nodes": [1]}', '{"nodes": [1]}'),
        ],
    )
    def test_reasoning_wrapped_json_is_recovered(self, raw: str, expected: str) -> None:
        """A leaked reasoning WRAPPER around JSON makes it non-parseable, so the
        wrapper is stripped and the clean JSON is recovered (this is what lets
        json.loads succeed on a reasoning model's wrapped graph-spec output)."""
        assert strip_reasoning_artifacts(raw) == expected


class _FakeSiteConfig:
    """Minimal SiteConfig stand-in — only the .get(key, default) seam matters."""

    def __init__(self, value: Any = None, raise_on_get: bool = False) -> None:
        self._value = value
        self._raise = raise_on_get

    def get(self, key: str, default: str = "") -> str:
        if self._raise:
            raise RuntimeError("site_config exploded")
        if self._value is None:
            return default
        return self._value


# ---------------------------------------------------------------------------
# is_thinking_model
# ---------------------------------------------------------------------------


class TestIsThinkingModel:
    @pytest.mark.parametrize(
        "model",
        [
            "qwen3:30b",
            "qwen3.5:35b",
            "ollama/qwen3:8b",
            "QWEN3-CODER:30B",  # case-insensitive
            "glm-4.7-5090:latest",
            "ollama/glm-4.7-5090",
            "deepseek-r1:7b",
        ],
    )
    def test_recognizes_canonical_thinking_models(self, model: str) -> None:
        assert is_thinking_model(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            "gemma3:27b",
            "gemma3:27b-it-qat",
            "llama3:latest",
            "ollama/gemma3:27b",
            "anthropic/claude-haiku-4-5",
            "phi3:latest",
        ],
    )
    def test_returns_false_for_non_thinking_models(self, model: str) -> None:
        assert is_thinking_model(model) is False

    def test_empty_string_returns_false(self) -> None:
        assert is_thinking_model("") is False

    def test_substrings_override_takes_precedence(self) -> None:
        # Even a normally-thinking name is False if substrings exclude it.
        assert is_thinking_model("qwen3:30b", substrings=("gemma",)) is False
        # A normally-non-thinking name is True if substrings include it.
        assert is_thinking_model("gemma3:27b", substrings=("gemma",)) is True


# ---------------------------------------------------------------------------
# resolve_thinking_substrings
# ---------------------------------------------------------------------------


class TestResolveThinkingSubstrings:
    def test_none_site_config_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(None) == _DEFAULT_SUBSTRINGS

    def test_empty_value_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value="")) == _DEFAULT_SUBSTRINGS

    def test_malformed_json_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value="{not json")) == _DEFAULT_SUBSTRINGS

    def test_non_list_json_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value='{"foo": "bar"}')) == _DEFAULT_SUBSTRINGS

    def test_empty_list_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value="[]")) == _DEFAULT_SUBSTRINGS

    def test_site_config_get_raising_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(raise_on_get=True)) == _DEFAULT_SUBSTRINGS

    def test_valid_json_list_is_lowercased_and_returned(self) -> None:
        sc = _FakeSiteConfig(value='["FOO","Bar","baz"]')
        assert resolve_thinking_substrings(sc) == ("foo", "bar", "baz")

    def test_operator_can_add_new_family(self) -> None:
        # The point of the registry: an operator drops in a new family
        # and the helpers immediately recognize it.
        sc = _FakeSiteConfig(value='["qwen3","glm-4","deepseek-r1","newmodel-r1"]')
        substrings = resolve_thinking_substrings(sc)
        assert is_thinking_model("ollama/newmodel-r1:7b", substrings=substrings) is True

    def test_operator_can_remove_family(self) -> None:
        # Edit the JSON to drop "deepseek-r1" and the helper stops
        # recognizing it without a code deploy.
        sc = _FakeSiteConfig(value='["qwen3","glm-4"]')
        substrings = resolve_thinking_substrings(sc)
        assert is_thinking_model("deepseek-r1:7b", substrings=substrings) is False
