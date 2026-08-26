"""utils.exception_format — the anti-`str(exc) == ""` helper.

The whole point of ``describe_exception`` is that timeout-class exceptions
(httpx.ReadTimeout, asyncio.TimeoutError, …) stringify to the EMPTY STRING,
so a bare ``f"failed: {exc}"`` produces a failure record naming no cause
(poindexter#3229). These tests pin both halves of the contract: empty
message ⇒ type name alone; real message ⇒ type-prefixed message.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from utils.exception_format import describe_exception


class TestDescribeException:
    def test_empty_message_renders_type_name(self):
        assert describe_exception(httpx.ReadTimeout("")) == "ReadTimeout"

    def test_asyncio_timeout_renders_type_name(self):
        assert describe_exception(asyncio.TimeoutError()) == "TimeoutError"

    def test_message_is_type_prefixed(self):
        assert describe_exception(RuntimeError("gpu busy")) == "RuntimeError: gpu busy"

    def test_whitespace_only_message_treated_as_empty(self):
        assert describe_exception(ValueError("   ")) == "ValueError"

    def test_cancelled_error_base_exception_accepted(self):
        # CancelledError subclasses BaseException, not Exception — the helper
        # must accept it (signature is BaseException on purpose).
        assert describe_exception(asyncio.CancelledError()) == "CancelledError"

    @pytest.mark.parametrize(
        "exc, expected",
        [
            (KeyError("missing"), "KeyError: 'missing'"),
            (OSError(28, "No space left on device"), "OSError: [Errno 28] No space left on device"),
        ],
    )
    def test_stdlib_message_shapes_pass_through(self, exc, expected):
        assert describe_exception(exc) == expected
