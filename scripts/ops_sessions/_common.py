"""Shared helpers for the deterministic + local-LLM ops sessions.

Standalone by design: needs only Python + asyncpg + httpx + the installed
``brain`` package (for DB-URL resolution and operator notification). No
FastAPI app / SiteConfig / DI bootstrap — these run as short cron scripts.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import subprocess
import tomllib
from pathlib import Path

import asyncpg
import httpx

OPS_OLLAMA_URL = os.environ.get("OPS_OLLAMA_URL", "http://localhost:11434")
MODEL_TRIAGE = os.environ.get("OPS_OLLAMA_MODEL_TRIAGE", "llama3.2:3b")
MODEL_TESTFIX = os.environ.get("OPS_OLLAMA_MODEL_TESTFIX", "qwen2.5-coder:7b")

_LOG_DIR = Path.home() / ".poindexter" / "logs" / "claude-sessions"


class OllamaUnavailable(RuntimeError):
    """Raised when the local Ollama endpoint cannot be reached."""


def bootstrap_value(key: str) -> str:
    path = Path.home() / ".poindexter" / "bootstrap.toml"
    if not path.exists():
        return ""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return str(data.get(key, "") or "")


def db_url() -> str:
    from brain.bootstrap import resolve_database_url

    url = resolve_database_url()
    if not url:
        notify_fail(
            "Ops session cannot start",
            "No database URL resolved (bootstrap.toml / DATABASE_URL).",
            "ops_sessions",
        )
        raise SystemExit(2)
    return url


async def fetch_all(sql: str, *args) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(db_url())
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def parse_ollama_content(payload: dict) -> str:
    return payload["message"]["content"]


def ollama_chat(
    user: str,
    *,
    model: str,
    system: str | None = None,
    as_json: bool = False,
    timeout: float = 120.0,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    body: dict = {"model": model, "messages": messages, "stream": False}
    if as_json:
        body["format"] = "json"
    try:
        resp = httpx.post(f"{OPS_OLLAMA_URL}/api/chat", json=body, timeout=timeout)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise OllamaUnavailable(f"{OPS_OLLAMA_URL}: {exc}") from exc
    return parse_ollama_content(resp.json())


def run(cmd: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    logging.getLogger("ops").info("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        logging.getLogger("ops").warning("rc=%s stderr=%s", proc.returncode, proc.stderr[:500])
    return proc


def gh(*args: str) -> subprocess.CompletedProcess:
    return run(["gh", *args])


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=cwd)


def notify_fail(title: str, detail: str, source: str) -> None:
    try:
        from brain.operator_notifier import notify_operator

        notify_operator(title, detail, source=source, severity="warning")
    except Exception:  # noqa: BLE001 — notification must never mask the real error
        logging.getLogger("ops").warning("notify_operator failed: %s | %s", title, detail)


def get_logger(name: str) -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    handler = logging.FileHandler(_LOG_DIR / f"{name}-{ts}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def asyncio_run(coro):
    return asyncio.run(coro)
