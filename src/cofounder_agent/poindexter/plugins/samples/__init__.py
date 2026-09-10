"""Sample plugins that ship with Poindexter core.

These are real implementations — not mocks or placeholders — but they
cover only the simplest cases of each Protocol. They serve two purposes:

1. **Prove the framework works end-to-end.** Each sample is registered
   via ``entry_points`` in the main ``pyproject.toml``. When the worker
   boots, the registry discovers them and the runner invokes them.
2. **Serve as authoritative reference** for plugin authors writing
   third-party Taps, Probes, Jobs, Stages, or LLMProviders. The
   samples show the exact Protocol shape and interaction with
   ``PluginConfig`` / ``PluginScheduler`` / the DB pool.

The real migrations — all ``auto-embed.py`` phases into Taps, all
``idle_worker.py`` methods into Jobs, all ``health_probes.py`` functions
into Probes, ``ollama_client.py`` into ``OllamaNativeProvider`` —
happen in Phases B, C, D, J respectively.
"""
