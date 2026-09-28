"""``poindexter.brain.sentry_scrub`` — credentials never leave in a Sentry payload.

GlitchTip held a Discord webhook token (8,981 breadcrumbs), a Telegram bot
token (250), the Postgres password and API keys, from http breadcrumbs, stack
frame locals and subprocess command lines (measured 2026-09-28). These tests
pin the scrubber's patterns, its walk over breadcrumbs and serialized events,
and the fail-closed contract its hooks owe the SDK. Every token here is fake.

The end-to-end check, through a real ``sentry_sdk`` client, is
``test_sentry_scrub_e2e.py``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from poindexter.brain import sentry_scrub
from poindexter.brain.sentry_scrub import (
    BUILTIN_PATTERNS,
    DEFAULT_PATTERNS,
    FILTERED,
    compile_patterns,
    init_options,
    scrub_breadcrumb,
    scrub_event,
    scrub_text,
    setting_enabled,
)

pytestmark = pytest.mark.unit

TG = "123456789:AAFakeTelegramTokenForUnitTests_xyz"
DISCORD = "FakeDiscordWebhookToken-for_unit.tests"

# (pattern name, input, expected output). Every built-in pattern needs at least
# one row; test_every_builtin_pattern_has_a_case derives that from the list.
CASES: list[tuple[str, str, str]] = [
    (
        "telegram_bot_path",
        "https://api.telegram.org/bot123:FAKE/sendMessage",
        "https://api.telegram.org/bot[Filtered]/sendMessage",
    ),
    (
        "telegram_bot_path",
        f"https://api.telegram.org/file/bot{TG}/photos/p.jpg",
        "https://api.telegram.org/file/bot[Filtered]/photos/p.jpg",
    ),
    (
        # http.client's _send_output frame held the raw request bytes.
        "telegram_bot_path",
        f"b'POST /bot{TG}/sendMessage HTTP/1.1\\r\\nHost: api.telegram.org'",
        "b'POST /bot[Filtered]/sendMessage HTTP/1.1\\r\\nHost: api.telegram.org'",
    ),
    (
        "discord_webhook_path",
        f"https://discord.com/api/webhooks/123456789012345678/{DISCORD}?wait=true",
        "https://discord.com/api/webhooks/123456789012345678/[Filtered]?wait=true",
    ),
    (
        "discord_webhook_path",
        f"https://discordapp.com/api/v10/webhooks/42/{DISCORD}/messages/7",
        "https://discordapp.com/api/v10/webhooks/42/[Filtered]/messages/7",
    ),
    # The breadcrumb's http.query field has no leading "?".
    ("query_secret", "key=FAKEKEY&page=2", "key=[Filtered]&page=2"),
    (
        "query_secret",
        "https://x.test/cb?code=200&access_token=FAKE#frag",
        "https://x.test/cb?code=200&access_token=[Filtered]#frag",
    ),
    (
        "query_secret",
        "https://eia.test/v2/rates?API_KEY=FAKEKEY&frequency=monthly",
        "https://eia.test/v2/rates?API_KEY=[Filtered]&frequency=monthly",
    ),
    (
        "query_secret",
        "X-Amz-Credential=minio%2F20260723&X-Amz-Date=20260723T151714Z&X-Amz-Signature=deadbeef",
        "X-Amz-Credential=[Filtered]&X-Amz-Date=20260723T151714Z&X-Amz-Signature=[Filtered]",
    ),
    (
        "url_userinfo_password",
        "postgresql://poindexter:FAKEPW@postgres-local:5432/poindexter_brain",
        "postgresql://poindexter:[Filtered]@postgres-local:5432/poindexter_brain",
    ),
    ("url_userinfo_password", "redis://:FAKEPW@redis:6379/0", "redis://:[Filtered]@redis:6379/0"),
    (
        # A restore-test subprocess breadcrumb carried both shapes.
        "env_secret_assignment",
        "docker exec -e DATABASE_URL=postgresql://postgres:FAKEPW@h:5432/db "
        "-e POSTGRES_PASSWORD=FAKEPW2 img",
        "docker exec -e DATABASE_URL=postgresql://postgres:[Filtered]@h:5432/db "
        "-e POSTGRES_PASSWORD=[Filtered] img",
    ),
    ("env_secret_assignment", "OPENAI_API_KEY=FAKEKEY python x.py", "OPENAI_API_KEY=[Filtered] python x.py"),
    (
        "authorization_header",
        "GET /v1/x HTTP/1.1\r\nAuthorization: Bearer FAKEOPAQUETOKEN1234567\r\n",
        "GET /v1/x HTTP/1.1\r\nAuthorization: Bearer [Filtered]\r\n",
    ),
    (
        # A headers dict held in a frame: the whole value goes, scheme and all.
        "repr_secret_item",
        "{'Authorization': 'Bearer FAKEOPAQUETOKEN1234567', 'Accept': 'application/json'}",
        "{'Authorization': '[Filtered]', 'Accept': 'application/json'}",
    ),
    ("authorization_header", "Basic ZmFrZXVzZXI6ZmFrZXBhc3N3b3Jk", "Basic [Filtered]"),
    (
        "jwt",
        "cfg={'api_key': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKESIGNATUREabc'}",
        "cfg={'api_key': '[Filtered]'}",
    ),
    ("github_token", "token=ghp_" + "F" * 36, "token=[Filtered]"),
    ("github_token", "cfg github_pat_" + "F" * 40 + " end", "cfg [Filtered] end"),
    ("sk_api_key", "base_url='x' sk-lf-FAKEFAKEFAKEFAKEFAKE12 end", "base_url='x' [Filtered] end"),
    (
        # pro_delivery's config, as its frame held it: the relay secret has no shape.
        "repr_secret_kwarg",
        "Cfg(ls_api_key='FAKEls', github_token=\"FAKEgh\", relay_secret='FAKE\\'relay', repo='o/r')",
        "Cfg(ls_api_key='[Filtered]', github_token=\"[Filtered]\", relay_secret='[Filtered]', repo='o/r')",
    ),
    (
        "repr_secret_item",
        "{'secret_key': 'deadbeef0123', 'bucket': 'media', 'max_tokens': '4096'}",
        "{'secret_key': '[Filtered]', 'bucket': 'media', 'max_tokens': '4096'}",
    ),
    ("repr_secret_item", '{"api_token": "FAKEcf", "zone": "z"}', '{"api_token": "[Filtered]", "zone": "z"}'),
]

# Text that looks near a credential but is not one. Each must come back as is.
CLEAN: list[str] = [
    "plain log line with nothing secret",
    "monkey=1&keyboard=2&tokens=5",  # not a secret key, and not after ? or &
    "cache miss key=user:42",  # a log line's key=, not a query string
    "ssh://git@github.com/org/repo.git",  # user without a password
    "https://example.com:8080/path@foo",  # a port, not a password
    "http://[::1]:8080/x",
    "Bearer authentication failed",  # prose, too short to be a token
    "2026-09-28T12:34:56Z worker ok",
    "https://api.telegram.org/getUpdates",
    "https://discord.com/api/v10/users/@me",
    "pg_advisory_lock wait exceeded 44.999968992000504s",
    "/api/webhooks/<id>/<token>",  # documentation placeholders
    "max_tokens=4096 tokenizer='bpe' author='me' keyboard='us'",  # names that only look close
    "if token == 'abc': pass",  # a comparison, not an assignment
    "{'bucket': 'media', 'tokens': '12'}",
]


@contextmanager
def _module_log_handler(handler: logging.Handler):
    """Attach ``handler`` to the scrubber's logger at DEBUG, then restore it."""
    target = logging.getLogger("poindexter.brain.sentry_scrub")
    level = target.level
    target.setLevel(logging.DEBUG)
    target.addHandler(handler)
    try:
        yield
    finally:
        target.removeHandler(handler)
        target.setLevel(level)


class TestBuiltinPatterns:
    @pytest.mark.parametrize(("name", "text", "expected"), CASES, ids=[c[0] for c in CASES])
    def test_redacts(self, name, text, expected):
        assert scrub_text(text) == expected

    @pytest.mark.parametrize(("name", "text", "expected"), CASES, ids=[c[0] for c in CASES])
    def test_case_is_matched_by_its_own_pattern(self, name, text, expected):
        """Each case matches the pattern it is filed under, so a pattern cannot
        keep "passing" on another pattern's account after it breaks."""
        regex = next(r for n, r, _s in BUILTIN_PATTERNS if n == name)
        assert re.compile(regex).search(text), f"{name} does not match its own case"

    def test_every_builtin_pattern_has_a_case(self):
        names = [name for name, _regex, _replacement in BUILTIN_PATTERNS]
        assert len(names) == len(set(names)), "duplicate pattern names"
        uncovered = set(names) - {case[0] for case in CASES}
        assert not uncovered, f"built-in patterns with no test case: {sorted(uncovered)}"

    @pytest.mark.parametrize("text", CLEAN)
    def test_leaves_clean_text_alone(self, text):
        assert scrub_text(text) == text

    @pytest.mark.parametrize("text", [c[1] for c in CASES] + CLEAN)
    def test_idempotent(self, text):
        once = scrub_text(text)
        assert scrub_text(once) == once

    def test_fake_secrets_never_survive(self):
        for _name, text, _expected in CASES:
            out = scrub_text(text)
            for secret in ("FAKE", TG, DISCORD, "deadbeef", "ghp_", "github_pat_", "sk-lf-"):
                assert secret not in out, (secret, out)

    def test_no_catastrophic_backtracking(self):
        """A pattern edit that backtracks across a long string would stall
        every breadcrumb. Adversarial inputs, 16 KB each, must stay fast."""
        adversarial = [
            "KEY" * 5500,
            "A" * 16_000,
            "aZ9" * 5500,
            "abc:" * 4000,
            "http://" + "a:" * 8000,
            "eyJ" + "a" * 16_000,
            "Bearer " + "x" * 16_000 + " ",
            "a_" * 8000 + "=x",
            "secret_key='" + "x" * 16_000,
            "'api_token': '" + "y" * 16_000,
        ]
        started = time.perf_counter()
        for text in adversarial:
            scrub_text(text)
        assert time.perf_counter() - started < 2.0


class TestCompilePatterns:
    def setup_method(self):
        compile_patterns.cache_clear()

    @pytest.mark.parametrize("raw", ["", "   ", "[]"])
    def test_empty_means_builtins_only(self, raw):
        assert compile_patterns(raw) == DEFAULT_PATTERNS

    def test_extras_are_added_after_the_builtins(self):
        patterns = compile_patterns(json.dumps([["(sess=)[a-z0-9]+", r"\1<S>"]]))
        assert patterns[: len(DEFAULT_PATTERNS)] == DEFAULT_PATTERNS
        assert len(patterns) == len(DEFAULT_PATTERNS) + 1
        out = scrub_text("sess=abc123 via https://api.telegram.org/bot1:FAKE/x", patterns)
        assert out == "sess=<S> via https://api.telegram.org/bot[Filtered]/x"

    def test_extras_cannot_switch_the_builtins_off(self):
        """A setting that maps a credential back to itself still loses to the
        built-ins, which run first."""
        patterns = compile_patterns(json.dumps([["\\[Filtered\\]", "LEAKED"]]))
        out = scrub_text("https://api.telegram.org/bot1:FAKE/x", patterns)
        assert "FAKE" not in out

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            '{"a": 1}',  # not an array
            '[["only-one"]]',  # not a pair
            '[["(unclosed", "x"]]',  # bad regex
            '[["(a)", "\\\\9"]]',  # bad group reference, only raises at sub()
        ],
    )
    def test_invalid_value_logs_once_and_keeps_the_builtins(self, raw, caplog):
        with caplog.at_level(logging.ERROR, logger="poindexter.brain.sentry_scrub"):
            assert compile_patterns(raw) == DEFAULT_PATTERNS
            assert compile_patterns(raw) == DEFAULT_PATTERNS
        errors = [r for r in caplog.records if "sentry_secret_scrub_patterns is invalid" in r.getMessage()]
        assert len(errors) == 1

    def test_invalid_value_logged_from_inside_a_hook_does_not_recurse(self):
        """The SDK's logging integration turns that error into a breadcrumb and
        an event, both of which run the hooks, which compile the same bad value
        again before the first call has cached it."""
        calls = []

        class ReenteringHandler(logging.Handler):
            def emit(self, record):
                calls.append(record.getMessage())
                scrub_breadcrumb({"message": "x"}, None, patterns=lambda: compile_patterns("not json"))

        with _module_log_handler(ReenteringHandler()):
            assert compile_patterns("not json") == DEFAULT_PATTERNS
        assert len([c for c in calls if "is invalid" in c]) == 1


class TestScrubBreadcrumb:
    def _http_crumb(self) -> dict:
        # The shape sentry_sdk's stdlib integration records for a request.
        return {
            "type": "http",
            "category": "httplib",
            "timestamp": datetime(2026, 9, 28, tzinfo=UTC),
            "data": {
                "http.method": "POST",
                "url": f"https://api.telegram.org/bot{TG}/sendMessage",
                "http.query": "chat_id=5&token=FAKE",
                "http.fragment": "",
                "http.response.status_code": 200,
                "reason": "OK",
            },
        }

    def test_http_breadcrumb(self):
        crumb = self._http_crumb()
        out = scrub_breadcrumb(crumb, {})
        assert out is crumb  # updated in place, as the SDK expects back
        assert out["data"]["url"] == "https://api.telegram.org/bot[Filtered]/sendMessage"
        assert out["data"]["http.query"] == "chat_id=5&token=[Filtered]"
        assert out["data"]["http.response.status_code"] == 200
        assert out["timestamp"] == datetime(2026, 9, 28, tzinfo=UTC)

    def test_message_and_nested_data(self):
        crumb = {
            "type": "subprocess",
            "message": "docker run -e POSTGRES_PASSWORD=FAKEPW img",
            "data": {"urls": [f"https://discord.com/api/webhooks/1/{DISCORD}"], "pair": ("token=FAKE", 3)},
        }
        out = scrub_breadcrumb(crumb)
        assert out["message"] == "docker run -e POSTGRES_PASSWORD=[Filtered] img"
        assert out["data"]["urls"] == ["https://discord.com/api/webhooks/1/[Filtered]"]
        assert out["data"]["pair"] == ("token=[Filtered]", 3)

    def test_patterns_source_is_read_on_every_call(self):
        seen = []

        def source():
            seen.append(1)
            return compile_patterns(json.dumps([["(mine=)\\w+", r"\1<M>"]]))

        assert scrub_breadcrumb({"message": "mine=abc"}, None, patterns=source)["message"] == "mine=<M>"
        scrub_breadcrumb({"message": "x"}, None, patterns=source)
        assert len(seen) == 2

    def test_failure_drops_the_crumb_and_says_so(self, caplog):
        """The SDK keeps the ORIGINAL crumb when before_breadcrumb raises, so a
        scrub failure must return None, never raise."""

        def broken():
            raise RuntimeError("settings unreadable")

        with caplog.at_level(logging.WARNING, logger="poindexter.brain.sentry_scrub"):
            assert scrub_breadcrumb(self._http_crumb(), {}, patterns=broken) is None
        assert "dropped it" in caplog.text

    def test_self_referencing_data_costs_one_pass(self):
        """Raw breadcrumb data is whatever the caller passed. A dict holding
        itself twice would be 2**64 walks with a depth cap alone, and this
        runs on every log line."""
        data: dict = {"url": "https://x.test/?token=FAKE", "items": []}
        data["a"] = data
        data["b"] = data
        data["items"].append(data)
        started = time.perf_counter()
        out = scrub_breadcrumb({"data": data})
        assert time.perf_counter() - started < 1.0
        assert out["data"]["url"] == "https://x.test/?token=[Filtered]"
        assert out["data"]["a"] is data


def _serialized_event() -> dict:
    """A serialized error event with a credential in every place one was
    found, or could land: what before_send receives."""
    tg_url = f"https://api.telegram.org/bot{TG}/sendMessage"
    dc_url = f"https://discord.com/api/webhooks/123/{DISCORD}"
    return {
        "level": "error",
        "message": f"send failed for {dc_url}",
        "logentry": {"message": "POST %s failed", "params": [tg_url]},
        "breadcrumbs": {
            "values": [
                {"type": "http", "category": "httplib", "data": {"url": tg_url, "http.query": ""}},
                {"type": "subprocess", "category": "subprocess", "message": "psql postgresql://u:FAKEPW@h/db"},
            ]
        },
        "exception": {
            "values": [
                {
                    "type": "HTTPStatusError",
                    "value": f"Client error '404 Not Found' for url '{dc_url}'",
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "send_discord",
                                "context_line": "    resp = urllib.request.urlopen(req, timeout=10)",
                                "vars": {
                                    "secret_key": "'0123456789abcdef0123456789abcdef'",
                                    "target": f"'{dc_url}'",
                                    "webhook_url": f"'{dc_url}'",
                                    "headers": {"Authorization": "'Bearer FAKEOPAQUETOKEN1234567'"},
                                    "dsn": "'postgresql://poindexter:FAKEPW@postgres-local:5432/db'",
                                },
                            }
                        ]
                    },
                }
            ]
        },
        "extra": {"url": dc_url},
        "contexts": {"notify": {"target": tg_url}},
        "request": {"url": "https://api.test/cb", "query_string": "access_token=FAKE&x=1", "data": {"k": tg_url}},
        "tags": {"endpoint": dc_url},
        "fingerprint": [f"{dc_url}"],
        "spans": [{"description": f"POST {tg_url}", "data": {"url": tg_url}}],
    }


class TestScrubEvent:
    def test_every_string_in_the_event(self):
        event = _serialized_event()
        out = scrub_event(event, {})
        assert out is event
        dumped = json.dumps(out)
        for secret in ("FAKE", TG, DISCORD):
            assert secret not in dumped, secret
        # Positive controls: the redacted shapes are still there to read.
        frame = out["exception"]["values"][0]["stacktrace"]["frames"][0]
        # A local named as a secret is filtered whole; any other is pattern-scrubbed.
        assert frame["vars"]["secret_key"] == FILTERED
        assert frame["vars"]["webhook_url"] == FILTERED
        assert frame["vars"]["dsn"] == FILTERED
        assert frame["vars"]["target"] == "'https://discord.com/api/webhooks/123/[Filtered]'"
        assert "0123456789abcdef" not in dumped
        assert out["request"]["query_string"] == "access_token=[Filtered]&x=1"
        assert out["spans"][0]["description"] == "POST https://api.telegram.org/bot[Filtered]/sendMessage"

    def test_secret_named_locals_are_filtered_whole(self):
        """A hex R2 key sat in upload_to_r2's ``secret_key`` local in 555 events:
        its value has no shape, so the name has to decide."""
        frame_vars = {
            "secret_key": "'0123456789abcdef0123456789abcdef'",
            "api_token": "'opaque'",
            "relay_secret": {"nested": "x"},
            "token": None,  # kept: "the token was unset" is worth seeing
            "bucket": "'media'",
            "max_tokens": "4096",
        }
        out = scrub_event({"exception": {"values": [{"stacktrace": {"frames": [{"vars": frame_vars}]}}]}})
        got = out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        assert got == {
            "secret_key": FILTERED,
            "api_token": FILTERED,
            "relay_secret": FILTERED,
            "token": None,
            "bucket": "'media'",
            "max_tokens": "4096",
        }

    def test_names_outside_frame_locals_are_not_judged(self):
        """Only a frame's ``vars`` is filtered by name: in ``extra`` or a
        breadcrumb, a key like token_count is data the operator reads."""
        out = scrub_event({"extra": {"token_count": 42, "secret_fields": "['api_key']"}})
        assert out == {"extra": {"token_count": 42, "secret_fields": "['api_key']"}}

    def test_keys_and_non_strings_are_untouched(self):
        event = {"extra": {"token=FAKE": "v", "count": 3, "ok": True, "none": None, "ratio": 0.5}}
        out = scrub_event(event)
        assert out == {"extra": {"token=FAKE": "v", "count": 3, "ok": True, "none": None, "ratio": 0.5}}

    def test_depth_cap_replaces_the_deep_container(self, monkeypatch):
        monkeypatch.setattr(sentry_scrub, "_MAX_DEPTH", 3)
        out = scrub_event({"a": {"b": {"c": {"d": "https://x.test/?token=FAKE"}}}})
        assert out == {"a": {"b": {"c": FILTERED}}}

    def test_failure_drops_the_event_and_says_so(self, caplog):
        def broken():
            raise RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="poindexter.brain.sentry_scrub"):
            assert scrub_event(_serialized_event(), {}, patterns=broken) is None
        assert "rather than send it unscrubbed" in caplog.text

    def test_failure_logged_from_inside_a_hook_does_not_recurse(self):
        """A warning logged by a failing hook becomes a breadcrumb, which runs
        the hook again. The second failure must drop quietly, not log."""
        messages = []

        def broken():
            raise RuntimeError("boom")

        class ReenteringHandler(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())
                assert scrub_breadcrumb({"message": "x"}, None, patterns=broken) is None

        with _module_log_handler(ReenteringHandler()):
            assert scrub_event({"message": "x"}, None, patterns=broken) is None
        assert len(messages) == 1


class TestInitOptions:
    def test_the_four_kwargs(self):
        options = init_options()
        assert set(options) == {
            "include_local_variables",
            "before_breadcrumb",
            "before_send",
            "before_send_transaction",
        }
        assert options["include_local_variables"] is False
        crumb = options["before_breadcrumb"]({"data": {"url": "https://api.telegram.org/bot1:FAKE/x"}}, {})
        assert crumb["data"]["url"] == "https://api.telegram.org/bot[Filtered]/x"
        for hook in ("before_send", "before_send_transaction"):
            event = options[hook]({"message": "?token=FAKE"}, {})
            assert event["message"] == "?token=[Filtered]"

    def test_local_variables_and_extras_pass_through(self):
        compile_patterns.cache_clear()
        options = init_options(extra_patterns='[["(mine=)\\\\w+", "\\\\1<M>"]]', include_local_variables=True)
        assert options["include_local_variables"] is True
        assert options["before_send"]({"message": "mine=abc"}, {})["message"] == "mine=<M>"


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("secret_key", True), ("api_token", True), ("relay_secret", True), ("ls_api_key", True),
        ("webhook_url", True), ("dsn", True), ("github_token", True), ("apikey", True),
        ("x_api_key", True), ("password_hash", True), ("auth", True), ("cookies", True),
        ("_plugin_get_secret", True), ("Authorization", True),
        ("max_tokens", False), ("tokenizer", False), ("author", False), ("keyboard", False),
        ("key", False), ("signatures", False), ("post_url", False), ("headers", False), ("cfg", False),
    ],
)
def test_secret_name_rule(name, secret):
    assert bool(sentry_scrub._SECRET_NAME.search(name)) is secret


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), (" TRUE ", True), ("1", True), ("yes", True), ("on", True),
     ("false", False), ("", False), (None, False), ("0", False), ("off", False), ("nope", False)],
)
def test_setting_enabled(raw, expected):
    assert setting_enabled(raw) is expected
