"""Data-plane response schemas — canonical list envelope (poindexter#745).

The declarative data-plane surfaces (taps / retention / webhooks / publishers /
qa-gates) share one generic listing endpoint whose rows are surface-polymorphic
— each surface is a different table with a different column set. So the list
items are typed as free-form ``dict[str, Any]``; the envelope is what gets
canonicalized.
"""

from typing import Any

from schemas.database_response_models import ListResponse


class DataPlaneRowListResponse(ListResponse[dict[str, Any]]):
    """Data-plane surface listing — canonical offset envelope (poindexter#745).

    ``{items, total, limit, offset}`` via ``ListResponse[dict[str, Any]]``.
    Replaces the prior untyped ``dict[str, Any]`` body that used a ``rows`` key.
    Rows are surface-polymorphic (taps / retention / webhooks / publishers /
    qa-gates each have distinct columns), so items stay free-form dicts — the
    envelope is the part that's canonicalized. The endpoint returns the full set
    for a surface unpaginated, so ``offset`` is always 0 and ``limit`` equals
    ``total``. Operator Integrations & Admin surface (OAuth-JWT); no HTTP
    consumer — the data-plane CLI calls ``declarative_config_service.list_rows``
    directly.
    """
