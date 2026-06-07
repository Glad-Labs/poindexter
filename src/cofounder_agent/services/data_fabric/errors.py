"""DataFabric error types."""

from __future__ import annotations


class DataFabricError(Exception):
    def __init__(self, store: str, message: str, *, status_code: int | None = None):
        super().__init__(f"[{store}] {message}")
        self.store = store
        self.status_code = status_code
