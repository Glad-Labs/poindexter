"""Hidden deprecated-alias factory for the poindexter CLI (#1652).

The CLI-consolidation work folds flat top-level commands into noun-groups
(``poindexter approve`` → ``poindexter gates approve``;
``poindexter approve-publish`` → ``poindexter schedule approve``; …). The
poindexter CLI is part of the public product, so per the repo backcompat rule
each rename ships with a shim: the old flat name stays callable as a **hidden,
deprecated alias**.

``deprecated_alias`` builds that shim. The alias reuses the canonical command's
params (so argv parses identically), prints a one-line deprecation notice to
**stderr** — keeping ``--json`` stdout clean for piping — and delegates to the
canonical command's callback via :meth:`click.Context.invoke`. ``hidden=True``
keeps it out of ``--help`` while it remains reachable.
"""

from __future__ import annotations

from typing import Any

import click


def deprecated_alias(
    target: click.Command,
    *,
    name: str,
    new_path: str,
) -> click.Command:
    """Return a hidden alias of ``target`` registered under the old ``name``.

    Args:
        target: the canonical command (e.g. the ``gates approve`` subcommand)
            the alias forwards to. Its params are reused verbatim.
        name: the old flat command name to expose the alias under
            (e.g. ``"approve"`` / ``"list-pending"``).
        new_path: the canonical invocation shown in the deprecation warning,
            without the ``poindexter`` prefix (e.g. ``"gates approve"``).

    The alias parses the same params as ``target``, warns to stderr, then
    invokes ``target``'s callback with the parsed values.
    """

    @click.pass_context
    def _forward(ctx: click.Context, /, **kwargs: Any) -> None:
        click.secho(
            f"warning: `poindexter {name}` is deprecated and will be removed; "
            f"use `poindexter {new_path}` instead.",
            fg="yellow",
            err=True,
        )
        ctx.invoke(target, **kwargs)

    return click.Command(
        name=name,
        params=list(target.params),
        callback=_forward,
        help=f"[DEPRECATED] alias for `poindexter {new_path}`.",
        short_help=f"[DEPRECATED] use `poindexter {new_path}`.",
        hidden=True,
    )


__all__ = ["deprecated_alias"]
