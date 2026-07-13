"""Guards that the `poindexter alerts` group is registered + wired
(poindexter#848 — finishing the GH-28 DB-driven Grafana alert sync)."""

import click
import pytest

from poindexter.cli.app import main


def test_alerts_group_registered():
    assert "alerts" in main.commands


def test_alerts_subcommands():
    from poindexter.cli.alerts import alerts_group

    assert {"list", "show", "create", "enable", "disable", "delete", "sync"} <= set(
        alerts_group.commands
    )


def test_create_requires_query_and_threshold():
    from poindexter.cli.alerts import alerts_create

    query_param = next(p for p in alerts_create.params if p.name == "query")
    assert query_param.required is True
    threshold_param = next(p for p in alerts_create.params if p.name == "threshold")
    assert threshold_param.required is True
    assert threshold_param.type is click.FLOAT


def test_create_defaults_to_disabled():
    from poindexter.cli.alerts import alerts_create

    enabled_param = next(p for p in alerts_create.params if p.name == "enabled")
    assert enabled_param.default is False


def test_create_has_sensible_duration_and_severity_defaults():
    from poindexter.cli.alerts import alerts_create

    duration_param = next(p for p in alerts_create.params if p.name == "duration")
    assert duration_param.default == "5m"
    severity_param = next(p for p in alerts_create.params if p.name == "severity")
    assert severity_param.default == "warning"


def test_create_labels_and_annotations_are_repeatable():
    from poindexter.cli.alerts import alerts_create

    labels_param = next(p for p in alerts_create.params if p.name == "labels")
    assert labels_param.multiple is True
    annotations_param = next(p for p in alerts_create.params if p.name == "annotations")
    assert annotations_param.multiple is True


def test_parse_kv_pairs_splits_on_first_equals():
    from poindexter.cli.alerts import _parse_kv_pairs

    assert _parse_kv_pairs(("summary=a=b",), opt_name="--annotation") == {"summary": "a=b"}


def test_parse_kv_pairs_rejects_missing_equals():
    from poindexter.cli.alerts import _parse_kv_pairs

    with pytest.raises(click.BadParameter):
        _parse_kv_pairs(("nope",), opt_name="--label")
