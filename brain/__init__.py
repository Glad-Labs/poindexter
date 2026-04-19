"""brain — Poindexter brain daemon + shared helpers.

Modules here are designed to be importable both as flat modules (inside
the brain Docker container where everything lands in /app/) and as the
`brain.` package (from other services that mount the brain directory).
"""
