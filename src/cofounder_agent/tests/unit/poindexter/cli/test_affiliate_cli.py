"""Guards that the `poindexter affiliate` group is registered + wired."""

from poindexter.cli.app import main


def test_affiliate_group_registered():
    assert "affiliate" in main.commands


def test_affiliate_subcommands():
    from poindexter.cli.affiliate import affiliate_group

    assert {"add", "list", "enable", "disable", "rm"} <= set(affiliate_group.commands)


def test_add_requires_category_and_description():
    from poindexter.cli.affiliate import add_cmd

    param_names = {p.name for p in add_cmd.params}
    assert {"category", "description"} <= param_names

    category_param = next(p for p in add_cmd.params if p.name == "category")
    assert category_param.required is True
    assert set(category_param.type.choices) == {"service", "product"}

    description_param = next(p for p in add_cmd.params if p.name == "description")
    assert description_param.required is True
