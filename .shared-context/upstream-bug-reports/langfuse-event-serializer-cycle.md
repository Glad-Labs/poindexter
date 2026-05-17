# langfuse-python — `EventSerializer.default` recurses forever on cyclic graphs (dict/list/`__slots__` branches missing cycle detection)

**Target repo:** https://github.com/langfuse/langfuse-python
**Affects:** Every published version including the latest 4.6.1 (and all of 3.x)
**Severity:** High — silently blocks the asyncio event loop in any host process

---

## Summary

`langfuse._utils.serializer.EventSerializer.default` has cycle detection on
the `__dict__` branch only. The `dict`, `list`, and `__slots__` branches
recurse without checking `self.seen`, so any object graph that contains a
cycle through one of those types makes the serializer recurse forever.

The `RecursionError` is eventually raised inside Python's GC machinery
("Exception ignored in: `<function WeakValueDictionary.__init__.<locals>.remove>`"),
which Python swallows. By that point the asyncio event loop has been
GIL-starved for minutes.

## Affected source

`langfuse/_utils/serializer.py` in 4.6.1 (line numbers approximate, same
structure in every published version):

```python
# Lines 129-132 — dict branch: NO cycle check
if isinstance(obj, dict):
    return {self.default(k): self.default(v) for k, v in obj.items()}

# Lines 134-136 — list branch: NO cycle check
if isinstance(obj, list):
    return [self.default(item) for item in obj]

# Lines 141-144 — __slots__ branch: NO cycle check
if hasattr(obj, "__slots__"):
    return self.default(
        {slot: getattr(obj, slot, None) for slot in obj.__slots__}
    )

# Lines 145-156 — __dict__ branch: HAS cycle check (the only one)
elif hasattr(obj, "__dict__"):
    obj_id = id(obj)
    if obj_id in self.seen:
        return type(obj).__name__   # <-- correctly breaks the cycle
    else:
        self.seen.add(obj_id)
        result = {k: self.default(v) for k, v in vars(obj).items()}
        self.seen.remove(obj_id)
        return result
```

## Minimal repro (10 lines)

```python
from langfuse._utils.serializer import EventSerializer

d = {"a": 1}
d["self"] = d  # cycle through the dict branch

# This hangs the interpreter at the recursion limit
# (RecursionError eventually raised inside a GC finalizer
# and SILENTLY SWALLOWED). With cycle detection, this
# would return something like '{"a": 1, "self": "<cycle:dict>"}'
EventSerializer().encode(d)
```

Same bug reproduces via `list`:

```python
lst = [1, 2, 3]
lst.append(lst)
EventSerializer().encode(lst)  # also hangs
```

And via `__slots__`-only objects with a self-referential slot value.

## Production impact

Captured in production 2026-05-15. A worker process using the Langfuse
`@observe` decorator over an asyncio FastAPI app experienced seven
consecutive event-loop hangs over six hours — every ~60 minutes,
coinciding with an OAuth client_credentials token refresh that emitted
a log record with a cyclic payload through Langfuse's logging
integration. Each hang locked the asyncio loop until an external
watchdog killed the container (~5 minutes of dead time per hang).

`faulthandler.dump_traceback_later(90)` captured the MainThread cycling
between the dict, list, and `__slots__` branches of `serializer.py` for
the full ~150 frames the dump cap allows. Stacks available on request.

## Suggested fix

Apply the same `seen`-id pattern that already exists on the `__dict__`
branch to the other three branches. Sketch:

```python
def default(self, obj):
    # ... unchanged for BaseModel, Path, Serializable, int, str, etc ...

    if isinstance(obj, (tuple, set, frozenset)):
        return list(obj)

    if isinstance(obj, dict):
        obj_id = id(obj)
        if obj_id in self.seen:
            return f"<cycle:{type(obj).__name__}>"
        self.seen.add(obj_id)
        try:
            return {self.default(k): self.default(v) for k, v in obj.items()}
        finally:
            self.seen.discard(obj_id)

    if isinstance(obj, list):
        obj_id = id(obj)
        if obj_id in self.seen:
            return f"<cycle:{type(obj).__name__}>"
        self.seen.add(obj_id)
        try:
            return [self.default(item) for item in obj]
        finally:
            self.seen.discard(obj_id)

    # Same pattern for the __slots__ branch.
```

`try/finally` ensures the id is removed on exit so the same object
appearing in a sibling subtree (not a cycle) doesn't get spuriously
marked as a cycle.

## Workaround (downstream)

Until a fix lands, a downstream consumer can monkey-patch
`EventSerializer.default` at import time:

```python
from langfuse._utils.serializer import EventSerializer

_original_default = EventSerializer.default

def _cycle_safe_default(self, obj):
    is_dict_or_list = isinstance(obj, (dict, list))
    is_slots_only = hasattr(obj, "__slots__") and not hasattr(obj, "__dict__")
    if is_dict_or_list or is_slots_only:
        obj_id = id(obj)
        if obj_id in self.seen:
            return f"<cycle:{type(obj).__name__}>"
        self.seen.add(obj_id)
        try:
            return _original_default(self, obj)
        finally:
            self.seen.discard(obj_id)
    return _original_default(self, obj)

EventSerializer.default = _cycle_safe_default
```

(Idempotent if guarded by an attribute flag.)

## Environment

- langfuse 4.6.1 (Python wheel from PyPI)
- Python 3.13.13
- Linux/glibc (Docker, `python:3.13-slim`)
- httpx 0.x via langfuse's own deps

Happy to provide additional context / stacks / a draft PR if useful.
