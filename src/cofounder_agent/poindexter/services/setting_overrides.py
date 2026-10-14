"""Plan- and step-scoped setting overrides for architect plans.

Matt (2026-10-13): "make Kai calmer in this one" should not make Kai calmer
everywhere. A plan spec may carry ``setting_overrides`` at the top level
(every step) and in any node's ``config`` (that step only; the step wins).
Each atom then runs with :meth:`SiteConfig.with_overrides`, a view that
answers those keys first. ``app_settings`` is never written, so an
experiment cannot leak into the blog pipeline or the next piece.

Only creative settings may be overridden. ``architect_setting_overrides_allow``
lists the permitted key patterns (fnmatch), ``architect_setting_overrides_deny``
the ones that stay out even when a pattern allows them: models (cost),
endpoints, paths and anything credential-shaped. A key must also already exist
(a registered default or a stored row), and its value must match the
registered type. The architect gets FIX lines for each problem at compose
time, and :func:`apply_overrides` re-filters at run time, so a hand-written
spec cannot reach a key the policy refuses either.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

ALLOW_KEY = "architect_setting_overrides_allow"
DENY_KEY = "architect_setting_overrides_deny"


def _patterns(site_config: Any, key: str) -> list[str]:
    from poindexter.services.settings_defaults import DEFAULTS

    raw = site_config.get(key, None) if site_config is not None else None
    csv = DEFAULTS[key] if raw is None else str(raw)
    return [p.strip() for p in csv.split(",") if p.strip()]


def is_overridable(key: str, site_config: Any) -> bool:
    if any(fnmatch.fnmatchcase(key, p) for p in _patterns(site_config, DENY_KEY)):
        return False
    return any(fnmatch.fnmatchcase(key, p) for p in _patterns(site_config, ALLOW_KEY))


def _known(key: str, site_config: Any) -> bool:
    from poindexter.services.settings_defaults import DEFAULTS

    if key in DEFAULTS:
        return True
    getter = getattr(site_config, "all", None)
    return bool(callable(getter) and key in getter())


def _type_error(key: str, value: str) -> str | None:
    from poindexter.services.settings_defaults import METADATA

    kind = str((METADATA.get(key) or {}).get("value_type") or "string")
    try:
        if kind == "integer":
            int(value)
        elif kind == "float":
            float(value)
        elif kind == "boolean" and value.strip().lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            return f"FIX setting_overrides: {key} is a boolean; use true or false"
        elif kind == "json":
            json.loads(value)
    except (TypeError, ValueError):
        article = "an" if kind[:1] in "aeiou" else "a"
        return f"FIX setting_overrides: {key} must be {article} {kind}, not {value!r}"
    return None


def check_overrides(overrides: Any, site_config: Any, *, where: str = "plan") -> list[str]:
    """FIX lines for one ``setting_overrides`` dict (``where`` names its node)."""
    if overrides in (None, {}):
        return []
    if not isinstance(overrides, dict):
        return [f"FIX {where}: setting_overrides must be an object of setting key to value"]
    errors: list[str] = []
    for key, value in overrides.items():
        key = str(key)
        if isinstance(value, (dict, list)):
            errors.append(f"FIX {where}: setting_overrides.{key} must be a single value")
            continue
        if not is_overridable(key, site_config):
            errors.append(
                f"FIX {where}: {key} cannot be overridden in a plan; drop it "
                "(only the settings listed under SETTING OVERRIDES can)"
            )
            continue
        if not _known(key, site_config):
            errors.append(f"FIX {where}: {key} is not a setting; use a key listed under SETTING OVERRIDES")
            continue
        text = "" if value is None else str(value)
        problem = _type_error(key, text)
        if problem:
            errors.append(problem.replace("FIX setting_overrides", f"FIX {where}"))
            continue
        if _same(key, text, site_config):
            errors.append(
                f"FIX {where}: {key} is already {text!r}, so this override changes nothing. "
                "Write the new value the intent asks for, or drop the key"
            )
    return errors


def _same(key: str, value: str, site_config: Any) -> bool:
    """An override equal to the value in force: the architect copied it unchanged."""
    from poindexter.services.settings_defaults import DEFAULTS

    getter = getattr(site_config, "all", None)
    snapshot = getter() if callable(getter) else {}
    current = snapshot.get(key, DEFAULTS.get(key))
    if current is None:
        return False
    try:
        return float(current) == float(value)
    except (TypeError, ValueError):
        return str(current).strip() == value.strip()


def check_spec_overrides(spec: dict[str, Any], site_config: Any) -> list[str]:
    """FIX lines for every ``setting_overrides`` in a plan spec."""
    errors = check_overrides(spec.get("setting_overrides"), site_config, where="plan")
    for node in spec.get("nodes") or []:
        if isinstance(node, dict) and isinstance(node.get("config"), dict):
            errors += check_overrides(
                node["config"].get("setting_overrides"), site_config, where=f"node {node.get('id')!r}",
            )
    return errors


def merged_overrides(plan: Any, step: Any) -> dict[str, str]:
    """The plan's overrides with the step's on top (the step wins)."""
    out: dict[str, str] = {}
    for layer in (plan, step):
        if isinstance(layer, dict):
            out.update({str(k): "" if v is None else str(v) for k, v in layer.items()})
    return out


def apply_overrides(site_config: Any, overrides: dict[str, str], *, where: str = "") -> Any:
    """``site_config`` seen through ``overrides``, keeping only permitted keys."""
    allowed = {k: v for k, v in overrides.items() if is_overridable(k, site_config)}
    refused = sorted(set(overrides) - set(allowed))
    if refused:
        logger.warning("[setting_overrides] %s: refused %s (not overridable)", where or "step", refused)
    if not allowed:
        return site_config
    with_overrides = getattr(site_config, "with_overrides", None)
    if not callable(with_overrides):
        logger.warning("[setting_overrides] %s: site_config cannot take overrides", where or "step")
        return site_config
    logger.info("[setting_overrides] %s: %s", where or "step", allowed)
    return with_overrides(allowed)


def describe_overrides(spec: dict[str, Any]) -> list[str]:
    """One line per override in a plan spec, for the plan card."""
    lines = [
        f"every step: {k} = {v}"
        for k, v in merged_overrides(spec.get("setting_overrides"), None).items()
    ]
    for node in spec.get("nodes") or []:
        config = node.get("config") if isinstance(node, dict) else None
        if isinstance(config, dict):
            for k, v in merged_overrides(None, config.get("setting_overrides")).items():
                lines.append(f"{node.get('id') or node.get('atom')}: {k} = {v}")
    return lines


def overridable_settings_block(site_config: Any, *, value_chars: int = 90) -> str:
    """The keys a plan may override, with current values, for the architect's prompt."""
    from poindexter.services.settings_defaults import DEFAULTS

    current: dict[str, str] = {}
    getter = getattr(site_config, "all", None)
    if callable(getter):
        current = dict(getter())
    keys = sorted(k for k in {*DEFAULTS, *current} if is_overridable(k, site_config))
    lines = []
    for key in keys:
        value = str(current.get(key, DEFAULTS.get(key, "")))
        if len(value) > value_chars:
            value = value[: value_chars - 3] + "..."
        lines.append(f"- {key} = {value}")
    return "\n".join(lines) or "(none)"


__all__ = [
    "ALLOW_KEY",
    "DENY_KEY",
    "apply_overrides",
    "check_overrides",
    "check_spec_overrides",
    "describe_overrides",
    "is_overridable",
    "merged_overrides",
    "overridable_settings_block",
]
