"""Guards that the `poindexter affiliate` group is registered + wired."""

from poindexter.cli.app import main


def test_affiliate_group_registered():
    assert "affiliate" in main.commands


def test_affiliate_subcommands():
    from poindexter.cli.affiliate import affiliate_group

    assert {"add", "list", "enable", "disable", "rm"} <= set(affiliate_group.commands)
