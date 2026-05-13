"""``poindexter finance`` subcommands — Mercury read-only bank queries.

F1 (2026-05-13). Goes direct to the Mercury API rather than round-
tripping through the worker — Mercury is external + the worker
doesn't yet have a finance route surface. Pulls the API token from
``app_settings.mercury_api_key`` so the operator never
has to type it on the command line.

Subcommands:
- ``poindexter finance balance``   — list all accounts + balances
- ``poindexter finance transactions <account-id>`` — recent txns

This is the F1 smoke-test surface — once F2 ships the polling job
+ DB tables, the daily digest will be the primary read path and
these commands become diagnostic-only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta

import asyncpg
import click

from ._bootstrap import resolve_dsn


def _run(coro):
    return asyncio.run(coro)


async def _read_token(dsn: str) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE key = 'mercury_api_key'"
        )
    finally:
        await conn.close()
    token = (row["value"] if row else "") or ""
    return token.strip()


@click.group(name="finance", help="Mercury bank balances + transactions.")
def finance_group() -> None:
    pass


@finance_group.command("balance")
@click.option("--json", "json_output", is_flag=True, help="Emit raw JSON.")
def finance_balance(json_output: bool) -> None:
    """List every Mercury account + current/available balance."""
    from modules.finance.mercury_client import (
        MercuryAuthError,
        MercuryClient,
    )

    async def _go():
        dsn = resolve_dsn()
        token = await _read_token(dsn)
        if not token:
            raise click.ClickException(
                "app_settings.mercury_api_key is empty. "
                "Generate a Read-Only token at "
                "Mercury dashboard → Settings → API, then:\n\n"
                "  poindexter settings set mercury_api_key <token> "
                "--secret\n"
            )
        async with MercuryClient(token=token) as m:
            return await m.list_accounts()

    try:
        accounts = _run(_go())
    except MercuryAuthError as e:
        raise click.ClickException(str(e)) from e

    if json_output:
        click.echo(
            json.dumps(
                [
                    {
                        "id": a.id,
                        "name": a.name,
                        "type": a.type,
                        "kind": a.kind,
                        "current_balance": a.current_balance,
                        "available_balance": a.available_balance,
                    }
                    for a in accounts
                ],
                indent=2,
            )
        )
        return

    if not accounts:
        click.secho("No accounts returned by Mercury.", fg="yellow")
        return

    total = sum(a.current_balance for a in accounts)
    click.secho(f"Mercury — {len(accounts)} account(s)", fg="green", bold=True)
    for a in accounts:
        click.echo(
            f"  {a.name:30s}  {a.kind:18s}  "
            f"current ${a.current_balance:>14,.2f}  "
            f"available ${a.available_balance:>14,.2f}"
        )
    click.echo("  " + "-" * 78)
    click.echo(f"  {'TOTAL':30s}  {'':18s}  current ${total:>14,.2f}")


@finance_group.command("transactions")
@click.argument("account_id")
@click.option(
    "--days", type=int, default=30,
    help="Lookback window in days (default 30).",
)
@click.option(
    "--limit", type=int, default=50,
    help="Max rows to return in one page (default 50).",
)
@click.option("--json", "json_output", is_flag=True, help="Emit raw JSON.")
def finance_transactions(
    account_id: str, days: int, limit: int, json_output: bool,
) -> None:
    """List recent transactions for ``account_id``. Default 30-day window."""
    from modules.finance.mercury_client import (
        MercuryAuthError,
        MercuryClient,
    )

    async def _go():
        dsn = resolve_dsn()
        token = await _read_token(dsn)
        if not token:
            raise click.ClickException(
                "app_settings.mercury_api_key is empty — "
                "see `poindexter finance balance` for setup instructions."
            )
        start_d = date.today() - timedelta(days=days)
        async with MercuryClient(token=token) as m:
            return await m.list_transactions(
                account_id, start=start_d, limit=limit,
            )

    try:
        txns = _run(_go())
    except MercuryAuthError as e:
        raise click.ClickException(str(e)) from e

    if json_output:
        click.echo(
            json.dumps(
                [
                    {
                        "id": t.id,
                        "amount": t.amount,
                        "posted_at": t.posted_at,
                        "counterparty": t.counterparty,
                        "status": t.status,
                    }
                    for t in txns
                ],
                indent=2,
            )
        )
        return

    if not txns:
        click.secho(f"No transactions in last {days} day(s) for {account_id}.",
                    fg="yellow")
        return

    income = sum(t.amount for t in txns if t.amount > 0)
    expense = sum(-t.amount for t in txns if t.amount < 0)
    net = income - expense

    click.secho(
        f"Mercury — {len(txns)} txn(s), last {days} day(s)",
        fg="green", bold=True,
    )
    for t in txns:
        sign_color = "green" if t.amount > 0 else "red"
        click.echo(
            f"  {t.posted_at[:19]:20s}  ", nl=False,
        )
        click.secho(f"${t.amount:>12,.2f}", fg=sign_color, nl=False)
        click.echo(f"  {t.counterparty[:40]:40s}  [{t.status}]")
    click.echo("  " + "-" * 78)
    click.secho(f"  income:  ${income:>14,.2f}", fg="green")
    click.secho(f"  expense: ${expense:>14,.2f}", fg="red")
    click.echo(
        f"  net:     ${net:>14,.2f}  ({'positive' if net >= 0 else 'NEGATIVE'})"
    )


__all__ = ["finance_group"]


if __name__ == "__main__":
    sys.exit(finance_group())
