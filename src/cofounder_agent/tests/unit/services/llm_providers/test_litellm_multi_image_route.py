"""Several images to Ollama go one per message over /api/chat.

Ollama 0.32.1 serving qwen3-vl through llama-server drops every second image of
a request whose images share a message (the 1st, 3rd, 5th reach the model, the
2nd, 4th never do; HTTP 200, no warning). LiteLLM's ``ollama/`` route
(/api/generate) merges all images into one list, so it drops them too. Measured
2026-09-28 against the pinned judge with distinct, randomly numbered images and
no index in the text: N images in one message read back correctly in 0 of 10
requests (N = 2..10); one image per message over /api/chat in 10 of 10.

Contract pinned here:

- a request with more than one image to an Ollama model gets one image per
  message, and an ``ollama/`` model reaches LiteLLM as ``ollama_chat/``;
- the ENDPOINT is still chosen from the caller's resolved name: the pin in
  ``model_api_base_overrides`` (keyed on ``ollama/qwen3-vl...``) must still
  apply, or the judge would load onto the default endpoint's GPU; and the
  ``Completion`` keeps the ``ollama/`` identity the cost log and the pin use;
- everything else is untouched: text-only requests, single-image requests,
  and cloud models, which take several images natively.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.llm_providers.litellm_provider import (
    LiteLLMProvider,
    count_image_parts,
    one_image_per_message,
    pinned_api_base_for,
    route_multi_image_for_ollama,
)

_DEFAULT_BASE = "http://host.docker.internal:11434"
_VISION_BASE = "http://host.docker.internal:11435"
_JUDGE = "ollama/qwen3-vl:30b-a3b-instruct"


def _img(tag: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tag}"}}


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


class TestCountImageParts:
    def test_text_only_and_string_content_have_none(self):
        assert count_image_parts([{"role": "user", "content": "hi"}]) == 0
        assert count_image_parts([{"role": "user", "content": [_text("hi")]}]) == 0
        assert count_image_parts([]) == 0

    def test_it_counts_across_messages(self):
        msgs = [
            {"role": "user", "content": [_text("a"), _img("1"), _img("2")]},
            {"role": "user", "content": [_img("3")]},
            {"role": "user", "content": "question"},
        ]
        assert count_image_parts(msgs) == 3


class TestOneImagePerMessage:
    def test_images_get_a_message_each_then_the_text(self):
        msgs = [{"role": "user", "content": [_text("rate these"), _img("1"), _img("2"), _img("3")]}]
        out = one_image_per_message(msgs)
        assert [m["role"] for m in out] == ["user"] * 4
        assert [m["content"] for m in out] == [[_img("1")], [_img("2")], [_img("3")], [_text("rate these")]]

    def test_text_parts_on_either_side_of_the_images_stay_together_in_order(self):
        msgs = [{"role": "user", "content": [_text("before"), _img("1"), _text("between"), _img("2"), _text("after")]}]
        out = one_image_per_message(msgs)
        assert out[-1]["content"] == [_text("before"), _text("between"), _text("after")]

    def test_a_message_without_text_yields_only_image_messages(self):
        out = one_image_per_message([{"role": "user", "content": [_img("1"), _img("2")]}])
        assert [m["content"] for m in out] == [[_img("1")], [_img("2")]]

    def test_a_message_with_one_image_and_text_only_messages_pass_through_untouched(self):
        one = {"role": "user", "content": [_text("look"), _img("1")]}
        plain = {"role": "system", "content": "be terse"}
        out = one_image_per_message([plain, one])
        assert out[0] is plain and out[1] is one

    def test_the_other_keys_of_a_split_message_are_kept(self):
        msgs = [{"role": "user", "name": "judge", "content": [_img("1"), _img("2")]}]
        assert all(m["name"] == "judge" and m["role"] == "user" for m in one_image_per_message(msgs))

    def test_the_input_is_not_mutated(self):
        msgs = [{"role": "user", "content": [_text("q"), _img("1"), _img("2")]}]
        before = repr(msgs)
        one_image_per_message(msgs)
        assert repr(msgs) == before

    def test_the_conversation_order_around_the_split_message_is_kept(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [_text("q"), _img("1"), _img("2")]},
            {"role": "assistant", "content": "ok"},
        ]
        out = one_image_per_message(msgs)
        assert [m["role"] for m in out] == ["system", "user", "user", "user", "assistant"]


class TestRouteMultiImageForOllama:
    def test_one_image_is_left_alone(self):
        msgs = [{"role": "user", "content": [_text("q"), _img("1")]}]
        model, out = route_multi_image_for_ollama(_JUDGE, msgs)
        assert model == _JUDGE and out is msgs

    def test_text_only_is_left_alone(self):
        msgs = [{"role": "user", "content": "q"}]
        assert route_multi_image_for_ollama(_JUDGE, msgs) == (_JUDGE, msgs)

    def test_several_images_take_the_chat_route_one_per_message(self):
        msgs = [{"role": "user", "content": [_text("q"), _img("1"), _img("2")]}]
        model, out = route_multi_image_for_ollama(_JUDGE, msgs)
        assert model == "ollama_chat/qwen3-vl:30b-a3b-instruct"
        assert [m["content"] for m in out] == [[_img("1")], [_img("2")], [_text("q")]]

    def test_an_ollama_chat_model_keeps_its_route_but_still_gets_one_image_per_message(self):
        msgs = [{"role": "user", "content": [_img("1"), _img("2")]}]
        model, out = route_multi_image_for_ollama("ollama_chat/qwen3-vl:30b", msgs)
        assert model == "ollama_chat/qwen3-vl:30b" and len(out) == 2

    def test_images_already_one_per_message_still_take_the_chat_route(self):
        msgs = [
            {"role": "user", "content": [_text("Tile 1:"), _img("1")]},
            {"role": "user", "content": [_text("Tile 2:"), _img("2")]},
            {"role": "user", "content": "question"},
        ]
        model, out = route_multi_image_for_ollama(_JUDGE, msgs)
        assert model.startswith("ollama_chat/")
        assert [m["content"] for m in out] == [m["content"] for m in msgs]

    @pytest.mark.parametrize("cloud", [
        "anthropic/claude-sonnet-5", "openai/gpt-5", "gemini/gemini-2.5-flash", "vllm/some-model",
    ])
    def test_other_providers_take_several_images_natively(self, cloud):
        msgs = [{"role": "user", "content": [_img("1"), _img("2"), _img("3")]}]
        assert route_multi_image_for_ollama(cloud, msgs) == (cloud, msgs)


def _fake_response(text: str = "ok"):
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 3
    resp.usage.completion_tokens = 1
    resp.usage.total_tokens = 4
    resp.model_dump.return_value = {}
    return resp


def _config():
    return {
        "api_base": _DEFAULT_BASE,
        "allow_paid_base_url": "false",
        "model_api_base_overrides": {_JUDGE: _VISION_BASE},
    }


async def _complete(model, messages, **kwargs):
    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=_fake_response(),
    ) as mock_acomp:
        completion = await LiteLLMProvider().complete(
            messages=messages, model=model, _provider_config=_config(), **kwargs,
        )
    return completion, mock_acomp.await_args.kwargs


@pytest.mark.unit
@pytest.mark.asyncio
class TestProviderComplete:
    async def test_several_images_go_one_per_message_over_the_chat_route_and_keep_the_pin(self):
        msgs = [{"role": "user", "content": [_text("rate"), _img("1"), _img("2"), _img("3")]}]
        completion, kwargs = await _complete(_JUDGE, msgs, num_ctx=16384, max_tokens=300)

        assert kwargs["model"] == "ollama_chat/qwen3-vl:30b-a3b-instruct"
        assert [m["content"] for m in kwargs["messages"]] == [
            [_img("1")], [_img("2")], [_img("3")], [_text("rate")],
        ]
        # THE safety property: the pin was looked up under the caller's resolved name
        assert kwargs["api_base"] == _VISION_BASE
        assert kwargs["num_ctx"] == 16384 and kwargs["max_tokens"] == 300
        # the logged / pinned identity is the caller's, not the wire route's
        assert completion.model == _JUDGE

    async def test_a_bare_model_name_still_finds_its_pin(self):
        msgs = [{"role": "user", "content": [_img("1"), _img("2")]}]
        _, kwargs = await _complete("qwen3-vl:30b-a3b-instruct", msgs)
        assert kwargs["model"] == "ollama_chat/qwen3-vl:30b-a3b-instruct"
        assert kwargs["api_base"] == _VISION_BASE

    async def test_the_dispatchers_pin_decision_does_not_change(self):
        """The dispatcher decides GPU lock and context from ``pinned_api_base_for``
        BEFORE the provider runs, on the name the caller used."""
        assert pinned_api_base_for(_JUDGE, _config()) == _VISION_BASE

    async def test_one_image_keeps_the_generate_route_and_its_message(self):
        msgs = [{"role": "user", "content": [_text("rate"), _img("1")]}]
        _, kwargs = await _complete(_JUDGE, msgs)
        assert kwargs["model"] == _JUDGE
        assert kwargs["messages"] == msgs

    async def test_text_only_is_untouched(self):
        msgs = [{"role": "user", "content": "hi"}]
        _, kwargs = await _complete(_JUDGE, msgs)
        assert kwargs["model"] == _JUDGE and kwargs["messages"] == msgs

    async def test_a_model_without_a_pin_keeps_the_default_endpoint(self):
        msgs = [{"role": "user", "content": [_img("1"), _img("2")]}]
        _, kwargs = await _complete("ollama/gemma4:31b", msgs)
        assert kwargs["model"] == "ollama_chat/gemma4:31b"
        assert kwargs["api_base"] == _DEFAULT_BASE


@pytest.mark.unit
@pytest.mark.asyncio
class TestProviderStream:
    async def test_stream_takes_the_same_route(self):
        async def chunks():
            delta = MagicMock()
            delta.content = "hi"
            choice = MagicMock()
            choice.delta = delta
            choice.finish_reason = "stop"
            chunk = MagicMock()
            chunk.choices = [choice]
            yield chunk

        msgs = [{"role": "user", "content": [_text("q"), _img("1"), _img("2")]}]
        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=chunks()) as mock_acomp:
            tokens = [
                t async for t in LiteLLMProvider().stream(
                    messages=msgs, model=_JUDGE, _provider_config=_config(),
                )
            ]
        assert [t.text for t in tokens] == ["hi"]
        kwargs = mock_acomp.await_args.kwargs
        assert kwargs["model"] == "ollama_chat/qwen3-vl:30b-a3b-instruct"
        assert kwargs["api_base"] == _VISION_BASE
        assert len(kwargs["messages"]) == 3
