# poindexter

**This is a placeholder that reserves the name. It is not the Poindexter CLI.**

[Poindexter](https://github.com/Glad-Labs/poindexter) is a self-hosted,
open-source AI content pipeline — it researches, writes, fact-checks,
illustrates, and publishes long-form content on your own hardware, with every
tunable in a database instead of an env file. The `poindexter` command is its
operator CLI.

## Install the real thing

The CLI ships **with the stack**, not as a standalone wheel yet:

```bash
git clone https://github.com/Glad-Labs/poindexter
cd poindexter
docker compose -f docker-compose.consumer.yml up -d
```

The `poindexter` command is then available inside the worker container, or on
the host via `poetry -C src/cofounder_agent install`. Full setup:
https://gladlabs.mintlify.app

## Why a placeholder

The CLI is a thin adapter over the backend's service layer — by design, it
imports `services`, `plugins` and `modules` rather than carrying its own logic.
The backend currently uses those as flat top-level package names, which cannot
be published to PyPI: a wheel that installs a top-level `utils` or `config`
would shadow the real PyPI packages of the same name, and vice versa.

Making `pip install poindexter` real means moving the backend under a
`poindexter.*` namespace. That migration is tracked here:

**https://github.com/Glad-Labs/poindexter/issues/1046**

This placeholder is superseded the moment it lands. Until then, running
`poindexter` from this package prints the instructions above and exits
non-zero, so nothing downstream mistakes it for the tool.

## License

Apache-2.0 — same as the project.
