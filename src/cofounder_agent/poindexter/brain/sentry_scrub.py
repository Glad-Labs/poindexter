"""Keep credentials out of GlitchTip: scrub Sentry breadcrumbs and events.

The Sentry SDK records every outbound HTTP request as a breadcrumb, with the
URL's path and query string unredacted (``parse_url(..., sanitize=False)`` in
both the stdlib ``http.client`` integration and the httpx one). By default it
also ships each stack frame's local variables with an exception. Chat webhooks
and bot APIs carry their credential in the URL itself, so both surfaces stored
live secrets. Measured 2026-09-28 over the 8,499 events GlitchTip still held
(29 June to 28 September):

* 8,981 ``httplib`` breadcrumbs carried the Discord webhook token and 250 the
  Telegram bot token, in ``data.url``. The stdlib integration is one of the
  SDK's defaults, so switching the httpx integration off does not close this:
  the brain posts through ``urllib``.
* Stack-frame locals carried the same two, plus the Postgres DSN password
  (``gpu_scheduler`` and asyncpg's ``dsn``), the R2 secret access key
  (``upload_to_r2``'s ``secret_key``, 555 events), the Lemon Squeezy API key,
  a GitHub token and a relay secret (``pro_delivery``'s config), the
  newsletter relay secret and bearer tokens in request ``headers`` dicts, and
  a Cloudflare API token.
* Subprocess breadcrumbs carried a ``POSTGRES_PASSWORD=`` from a
  ``docker exec`` command line, and presigned S3 URLs their signatures.

Every ``sentry_sdk.init`` gets two defences from here: the worker and the
Prefect flow runs through ``services/sentry_integration.py``, the brain through
``brain_daemon._init_sentry``, the MCP HTTP server through
``mcp-server/http_server.py``.

1. ``include_local_variables`` is off unless ``sentry_include_local_variables``
   turns it on. No pattern list can know every secret shape a local variable
   might hold (a hex R2 key has none), so the complete fix for that surface is
   not to send it. When an operator does turn it on, a local whose name says
   secret (``secret_key``, ``api_token``, ``dsn``, ``webhook_url``) is
   filtered whole, and the patterns run over the rest.
2. Credential-shaped substrings become ``[Filtered]`` (the SDK's own marker)
   in each breadcrumb as it is recorded (``before_breadcrumb``), and in every
   string of an event or transaction just before it is sent
   (``before_send`` / ``before_send_transaction``). The second pass sees the
   serialized event, so it also covers what the first cannot: exception
   values, log messages, ``extra``, spans and whatever breadcrumb data only
   became a string on serialization.

The built-in patterns always apply. ``sentry_secret_scrub_patterns`` (a JSON
array of ``[regex, replacement]`` pairs) can add to them and never replace
them: a scrubber that one edited row could switch off would not be one.

Both hooks fail closed. The SDK keeps the ORIGINAL breadcrumb when
``before_breadcrumb`` raises, and silently drops the event when
``before_send`` does, so each hook catches its own failure, logs it, and
drops what it could not scrub rather than let it out unscrubbed.

Stdlib-only, and under ``poindexter/brain/`` because the brain image ships
that package and nothing else (``poindexter/brain/Dockerfile``). The worker
and the MCP server import it from here.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# The SDK's own substitute (``sentry_sdk.utils.SENSITIVE_DATA_SUBSTITUTE``), so
# a scrubbed value reads the same in GlitchTip whichever scrubber caught it.
FILTERED = "[Filtered]"

# A word that, as one underscore-separated part of a name, says the value is a
# credential: secret_key, api_token, relay_secret, webhook_url, dsn. Plurals
# and longer words stay out, so max_tokens, tokenizer, author and keyboard are
# not caught; token_count is, which costs one number.
_SECRET_WORDS = (
    r"(?:secret|password|passwd|pwd|token|dsn|credentials?|webhook|auth"
    r"|authorization|cookies?|signature"
    r"|(?:api|access|secret|private|signing|encryption|client)_?key)"
)
_SECRET_NAME = re.compile(rf"(?i)(?:^|_){_SECRET_WORDS}(?:_|$)")
# The same, as a whole identifier inside text: ls_api_key, _relay_secret.
_SECRET_IDENT = rf"_*(?:[A-Za-z0-9]+_)*{_SECRET_WORDS}(?:_[A-Za-z0-9]+)*"
# A quoted value of up to 512 characters, escapes included; \2 or \3 is its quote.
_QUOTED = r"(?:(?!\{q})[^\\\n]|\\.){{0,512}}\{q}"

# ``(name, regex, replacement)``. Every entry is either one the 2026-09-28
# measurement found stored in GlitchTip or one the task named outright, and
# each is anchored on the shape of a credential, or on a name that says it is
# one. Quantifiers that could backtrack across a long string are bounded.
BUILTIN_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # https://api.telegram.org/bot<id>:<token>/sendMessage, and /file/bot...:
    # the Bot API puts the whole token in the path.
    ("telegram_bot_path", r"(/bot)\d+:[A-Za-z0-9_-]+", r"\1" + FILTERED),
    # https://discord.com/api/webhooks/<id>/<token>, with or without /v10.
    (
        "discord_webhook_path",
        r"(/api(?:/v\d+)?/webhooks/\d+/)[A-Za-z0-9_.-]+",
        r"\1" + FILTERED,
    ),
    # A query-string value under a secret-ish key: ?key=, &access_token=,
    # X-Amz-Signature=. Only after ? & ; or at the start of the string (the
    # breadcrumb's ``http.query`` field has no leading ?), so ``monkey=`` and
    # a log line's ``key=`` are left alone.
    (
        "query_secret",
        r"(?i)((?:^|[?&;])(?:key|api_key|apikey|api-key|token|access_token"
        r"|refresh_token|id_token|auth_token|jwt|secret|client_secret"
        r"|signature|sig|password|passwd|pwd|x-amz-signature"
        r"|x-amz-credential|x-amz-security-token)=)[^&#\s'\"]+",
        r"\1" + FILTERED,
    ),
    # scheme://user:password@host, as in a DSN on a command line.
    (
        "url_userinfo_password",
        r"(\b[A-Za-z][A-Za-z0-9+.-]{0,30}://[^/\s:@'\"]{0,256}:)"
        r"[^/\s@'\"]{1,256}(@)",
        r"\1" + FILTERED + r"\2",
    ),
    # POSTGRES_PASSWORD=..., API_TOKEN=...: an environment assignment on a
    # command line (``docker exec -e``) or in an env dump.
    (
        "env_secret_assignment",
        r"\b([A-Z][A-Z0-9_]{0,63}(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD"
        r"|CREDENTIALS?)[A-Z0-9_]{0,63}=)[^\s'\"&]+",
        r"\1" + FILTERED,
    ),
    # An Authorization header value. 16+ characters, so prose such as
    # "Bearer authentication failed" is left alone.
    (
        "authorization_header",
        r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{16,}",
        r"\1" + FILTERED,
    ),
    # A JWT anywhere (OAuth access tokens, JWT-shaped API keys).
    (
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+",
        FILTERED,
    ),
    # GitHub personal-access, OAuth, app-installation and fine-grained tokens.
    (
        "github_token",
        r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})",
        FILTERED,
    ),
    # sk-... API keys: OpenAI-compatible, Anthropic's sk-ant-, Langfuse's sk-lf-.
    ("sk_api_key", r"\bsk-[A-Za-z0-9_-]{20,}", FILTERED),
    # A repr or keyword argument whose name says secret: a dataclass printed
    # into a log line or a frame, like pro_delivery's config
    # (ls_api_key='…', relay_secret='…'). The value has no shape to go on.
    (
        "repr_secret_kwarg",
        rf"(?i)\b({_SECRET_IDENT}\s*=\s*)(['\"])" + _QUOTED.format(q="2"),
        r"\1\2" + FILTERED + r"\2",
    ),
    # The same as a dict item or JSON member: 'secret_key': '…', "api_token": "…".
    (
        "repr_secret_item",
        rf"(?i)((['\"]){_SECRET_IDENT}\2\s*:\s*)(['\"])" + _QUOTED.format(q="3"),
        r"\1\3" + FILTERED + r"\3",
    ),
)

Patterns = tuple[tuple[re.Pattern[str], str], ...]

DEFAULT_PATTERNS: Patterns = tuple(
    (re.compile(regex), replacement) for _name, regex, replacement in BUILTIN_PATTERNS
)

# Deeper than any serialized event nests (the SDK caps a databag at 5 levels
# below its root). Past this, a container is replaced whole rather than
# walked: failing closed, as everywhere here.
_MAX_DEPTH = 64

# Set while this module is logging, so a log record that re-enters a hook
# (the SDK's logging integration turns records into breadcrumbs and events)
# cannot recurse into another log call.
_reentry = threading.local()


def _log(level: int, message: str, *args: Any, exc_info: bool = False) -> None:
    """Log without letting the record re-enter this module's logging."""
    if getattr(_reentry, "active", False):
        return
    _reentry.active = True
    try:
        logger.log(level, message, *args, exc_info=exc_info)
    finally:
        _reentry.active = False


@functools.lru_cache(maxsize=16)
def compile_patterns(extra_json: str = "") -> Patterns:
    """The built-in patterns plus the operator's extras, compiled once per value.

    ``extra_json`` is the raw ``sentry_secret_scrub_patterns`` value: a JSON
    array of ``[regex, replacement]`` pairs. Empty means none. An invalid
    value logs an error and adds nothing, and the built-ins still apply.
    Replacement templates are exercised here, because a bad group reference
    only raises when ``sub()`` runs, which would be once per breadcrumb.
    Never raises.
    """
    raw = (extra_json or "").strip()
    if not raw:
        return DEFAULT_PATTERNS
    extras: list[tuple[re.Pattern[str], str]] = []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise TypeError(f"expected a JSON array, got {type(parsed).__name__}")
        for entry in parsed:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise TypeError(f"expected [regex, replacement] pairs, got {entry!r}")
            pattern, replacement = re.compile(str(entry[0])), str(entry[1])
            pattern.sub(replacement, "")
            extras.append((pattern, replacement))
    except (ValueError, TypeError, re.error) as exc:
        _log(
            logging.ERROR,
            "[sentry_scrub] sentry_secret_scrub_patterns is invalid (%s) — "
            "ignoring it; the built-in credential patterns still apply. Fix "
            "the setting in app_settings.",
            exc,
        )
        return DEFAULT_PATTERNS
    return DEFAULT_PATTERNS + tuple(extras)


def scrub_text(text: str, patterns: Patterns = DEFAULT_PATTERNS) -> str:
    """Replace every credential-shaped substring of ``text`` with ``[Filtered]``."""
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def _filter_secret_names(frame_vars: dict[Any, Any]) -> None:
    """Filter a stack frame's locals whose NAME says they hold a credential.

    Their values often have no shape to give them away: the R2 secret key sat
    in ``upload_to_r2``'s ``secret_key`` local as 64 hex characters in 555
    stored events. Only matters when ``sentry_include_local_variables`` is on;
    ``None`` is kept, since "the token was unset" is worth seeing.
    """
    for name, value in frame_vars.items():
        if value is not None and isinstance(name, str) and _SECRET_NAME.search(name):
            frame_vars[name] = FILTERED


def _scrub(value: Any, patterns: Patterns, depth: int, seen: set[int]) -> Any:
    """Scrub every string in ``value``, recursing into dicts, lists and tuples.

    Dicts and lists are updated in place and returned; tuples are rebuilt.
    Keys are left alone: in an event they are field and variable names. A
    ``vars`` dict (a stack frame's locals, in the Sentry protocol) also has
    its secret-named entries filtered whole. Other types are returned
    untouched: in a breadcrumb they are serialized later, and ``before_send``
    sees the resulting string. ``seen`` holds the dicts and lists already
    walked, so raw breadcrumb data that refers to itself costs one pass, not
    one per path to it.
    """
    if isinstance(value, str):
        return scrub_text(value, patterns)
    if not isinstance(value, (dict, list, tuple)):
        return value
    if depth >= _MAX_DEPTH:
        return FILTERED
    if isinstance(value, tuple):
        return tuple(_scrub(item, patterns, depth + 1, seen) for item in value)
    if id(value) in seen:
        return value
    seen.add(id(value))
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "vars" and isinstance(item, dict):
                _filter_secret_names(item)
            value[key] = _scrub(item, patterns, depth + 1, seen)
    else:
        for index, item in enumerate(value):
            value[index] = _scrub(item, patterns, depth + 1, seen)
    return value


PatternSource = Patterns | Callable[[], Patterns] | None


def _resolve(patterns: PatternSource) -> Patterns:
    """``None`` means the built-ins; a callable is read on every call."""
    if patterns is None:
        return DEFAULT_PATTERNS
    if callable(patterns):
        return patterns()
    return patterns


def scrub_breadcrumb(
    crumb: dict[str, Any], hint: Any = None, *, patterns: PatternSource = None
) -> dict[str, Any] | None:
    """``before_breadcrumb``: scrub the message and every string in ``data``.

    ``patterns`` may be a callable, which is how the worker re-reads its
    setting on each call. It is resolved inside the guard, so a failure there
    drops the crumb like any other: the SDK would otherwise keep the original.
    """
    try:
        return _scrub(crumb, _resolve(patterns), 0, set())
    except Exception:  # noqa: BLE001 — the SDK keeps the unscrubbed crumb if this raises; drop it instead
        _log(
            logging.WARNING,
            "[sentry_scrub] could not scrub a breadcrumb; dropped it",
            exc_info=True,
        )
        return None


def scrub_event(
    event: dict[str, Any], hint: Any = None, *, patterns: PatternSource = None
) -> dict[str, Any] | None:
    """``before_send`` / ``before_send_transaction``: scrub every string.

    Runs on the serialized event, so everything in it is a dict, list, string,
    number, bool or ``None``: breadcrumbs, exception values, frames, log
    messages, ``extra``, ``contexts``, ``request``, tags, spans.
    """
    try:
        return _scrub(event, _resolve(patterns), 0, set())
    except Exception:  # noqa: BLE001 — an event that could not be scrubbed is dropped, never sent as is
        _log(
            logging.WARNING,
            "[sentry_scrub] could not scrub an event; dropped it rather than "
            "send it unscrubbed",
            exc_info=True,
        )
        return None


def setting_enabled(raw: Any) -> bool:
    """Truthiness of an ``app_settings`` boolean read as a string."""
    return str(raw or "").strip().lower() in ("true", "1", "yes", "on")


def init_options(
    *, extra_patterns: str = "", include_local_variables: bool = False
) -> dict[str, Any]:
    """The ``sentry_sdk.init`` keyword arguments that keep credentials out.

    For init sites that read their settings once at start (the brain, the MCP
    HTTP server). The worker wires the same functions through its own
    classmethods so its settings stay live.
    """
    patterns = compile_patterns(extra_patterns)
    return {
        "include_local_variables": include_local_variables,
        "before_breadcrumb": functools.partial(scrub_breadcrumb, patterns=patterns),
        "before_send": functools.partial(scrub_event, patterns=patterns),
        "before_send_transaction": functools.partial(scrub_event, patterns=patterns),
    }
