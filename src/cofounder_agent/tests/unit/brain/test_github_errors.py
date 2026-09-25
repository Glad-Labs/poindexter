"""Unit tests — brain/github_errors.py, shared by the two GitHub-calling probes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from poindexter.brain.github_errors import (
    TOKEN_FIX,
    GitHubAPIError,
    github_message,
    is_rate_limited,
)


class _Resp:
    def __init__(self, status_code: int, *, text: str = "", headers: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers if headers is not None else {}


class _BrokenHeaders:
    def get(self, _name: str) -> str:
        raise RuntimeError("unreadable")


@pytest.mark.unit
class TestRateLimit:
    def test_reads_the_header_or_the_body(self):
        assert is_rate_limited(_Resp(403, headers={"x-ratelimit-remaining": "0"}))
        assert is_rate_limited(_Resp(403, text="You have exceeded a secondary rate limit"))
        assert not is_rate_limited(_Resp(403, headers={"x-ratelimit-remaining": "4999"}))
        assert not is_rate_limited(_Resp(403, text='{"message":"Resource not accessible"}'))
        assert not is_rate_limited(_Resp(404, headers={"x-ratelimit-remaining": "0"}))

    def test_429_always_is_one(self):
        assert is_rate_limited(_Resp(429))

    def test_unreadable_headers_count_as_none(self):
        assert not is_rate_limited(_Resp(403, headers=_BrokenHeaders()))


@pytest.mark.unit
class TestGitHubMessage:
    def test_json_message(self):
        assert github_message(json.dumps({"message": "Bad credentials"})) == "Bad credentials"

    def test_html_is_compacted(self):
        assert github_message("<html>\n  <b>oops</b>\n</html>") == "<html> <b>oops</b> </html>"

    def test_empty_body(self):
        assert github_message("") == "no body"


@pytest.mark.unit
class TestGitHubAPIError:
    def test_from_response_keeps_what_the_page_needs(self):
        exc = GitHubAPIError.from_response(
            "commits/main", _Resp(404, text='{"message":"Not Found"}'), ref="abc",
        )
        assert (exc.endpoint, exc.status_code, exc.ref) == ("commits/main", 404, "abc")
        assert exc.rate_limited is False
        # The same text prod's audit rows have always carried, for grep continuity.
        assert str(exc) == 'GitHub /commits/main returned 404: {"message":"Not Found"}'

    @pytest.mark.parametrize(
        ("status", "headers", "transient"),
        [
            (401, {}, False),
            (403, {}, False),
            (403, {"x-ratelimit-remaining": "0"}, True),
            (404, {}, False),
            (429, {}, True),
            (502, {}, True),
            (503, {}, True),
        ],
    )
    def test_transient(self, status, headers, transient):
        exc = GitHubAPIError.from_response("pulls", _Resp(status, headers=headers))
        assert exc.transient is transient

    def test_token_fix_names_the_cli(self):
        assert TOKEN_FIX == "`poindexter settings set gh_token <token> --secret`"
