"""Guards that the `poindexter affiliate` group is registered + wired."""

import click
import pytest

from poindexter.cli.app import main


def test_affiliate_group_registered():
    assert "affiliate" in main.commands


def test_affiliate_subcommands():
    from poindexter.cli.affiliate import affiliate_group

    assert {"add", "list", "enable", "disable", "rm", "import-csv"} <= set(affiliate_group.commands)


def test_add_requires_category_and_description():
    from poindexter.cli.affiliate import add_cmd

    param_names = {p.name for p in add_cmd.params}
    assert {"category", "description"} <= param_names

    category_param = next(p for p in add_cmd.params if p.name == "category")
    assert category_param.required is True
    assert set(category_param.type.choices) == {"service", "product"}

    description_param = next(p for p in add_cmd.params if p.name == "description")
    assert description_param.required is True


def test_add_keyword_is_repeatable_and_required():
    from poindexter.cli.affiliate import add_cmd

    keyword_param = next(p for p in add_cmd.params if p.name == "keywords")
    assert keyword_param.multiple is True
    assert keyword_param.required is True


def test_add_has_optional_platform():
    from poindexter.cli.affiliate import add_cmd

    platform_param = next(p for p in add_cmd.params if p.name == "platform")
    assert platform_param.required is False
    assert platform_param.default == ""


def test_list_has_all_flag():
    from poindexter.cli.affiliate import list_cmd

    all_param = next(p for p in list_cmd.params if p.name == "show_all")
    assert all_param.is_flag is True


def test_enable_has_all_flag_and_optional_code():
    from poindexter.cli.affiliate import enable_cmd

    code_param = next(p for p in enable_cmd.params if p.name == "code")
    assert code_param.required is False
    all_param = next(p for p in enable_cmd.params if p.name == "enable_all")
    assert all_param.is_flag is True


def test_enable_validate_args_rejects_neither():
    from poindexter.cli.affiliate import _validate_enable_args

    with pytest.raises(click.UsageError):
        _validate_enable_args(None, False)


def test_enable_validate_args_rejects_both():
    from poindexter.cli.affiliate import _validate_enable_args

    with pytest.raises(click.UsageError):
        _validate_enable_args("mercury", True)


def test_enable_validate_args_accepts_code_only():
    from poindexter.cli.affiliate import _validate_enable_args

    _validate_enable_args("mercury", False)  # must not raise


def test_enable_validate_args_accepts_all_only():
    from poindexter.cli.affiliate import _validate_enable_args

    _validate_enable_args(None, True)  # must not raise


def test_import_csv_requires_existing_path():
    from poindexter.cli.affiliate import import_csv_cmd

    path_param = next(p for p in import_csv_cmd.params if p.name == "csv_path")
    assert path_param.type.exists is True


def test_import_csv_has_force_flag():
    from poindexter.cli.affiliate import import_csv_cmd

    force_param = next(p for p in import_csv_cmd.params if p.name == "force")
    assert force_param.is_flag is True
