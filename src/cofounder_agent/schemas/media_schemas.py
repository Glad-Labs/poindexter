"""Media-listing response schemas — canonical list envelopes (poindexter#745).

Podcast and video episode listings share the canonical
``{items, total, limit, offset}`` envelope via ``ListResponse``. These models
replace the prior untyped bodies that used bespoke ``episodes``/``count`` keys.
"""

from pydantic import BaseModel

from schemas.database_response_models import ListResponse


class PodcastEpisodeItem(BaseModel):
    """One generated podcast episode (rendered ``.mp3`` + filesystem metadata).

    Sourced from ``PodcastService.list_episodes`` — a directory scan of the
    podcast output dir. ``created_at`` is a Unix epoch float (``st_ctime``),
    NOT an ISO string, so it is typed ``float`` to preserve the wire format
    (typing it ``datetime`` would silently rewrite the float to ISO). Fields
    default permissively so a partial row never trips validation.

    ``file_path`` is deliberately NOT a field: the service dict carries the
    worker's absolute ``.mp3`` path for internal callers, but the response_model
    filters it out of the public HTTP body — completing the #636 hardening that
    removed on-disk paths from the sibling video listing (whose in-code comment
    already asserts podcast parity). Clients fetch bytes via
    ``/api/podcast/episodes/{post_id}.mp3``, never an absolute path.
    """

    post_id: str | None = None
    file_size_bytes: int | None = None
    created_at: float | None = None


class PodcastEpisodeListResponse(ListResponse[PodcastEpisodeItem]):
    """Podcast episode listing — canonical offset envelope (poindexter#745).

    ``{items, total, limit, offset}`` via ``ListResponse[PodcastEpisodeItem]``.
    Replaces the prior untyped body that used ``episodes``/``count`` keys
    (``count`` is recoverable as ``len(items)``). Public endpoint, but the JSON
    list has no in-repo consumer — podcast clients use the RSS ``/feed.xml``,
    not this list. Real ``limit``/``offset`` pagination (#746).
    """


class VideoEpisodeItem(BaseModel):
    """One generated video episode (rendered ``.mp4`` + filesystem metadata).

    Sourced from ``video_routes.list_video_episodes`` — a directory scan of the
    video output dir. ``created_at`` is a Unix epoch float (``st_ctime``), NOT
    an ISO string, so it is typed ``float`` to preserve the wire format. There
    is no ``file_path`` field: the route already omits the worker's absolute
    path (poindexter#636); clients fetch bytes via
    ``/api/video/episodes/{post_id}.mp4``. Fields default permissively so a
    partial row never trips validation.
    """

    post_id: str | None = None
    file_size_bytes: int | None = None
    created_at: float | None = None


class VideoEpisodeListResponse(ListResponse[VideoEpisodeItem]):
    """Video episode listing — canonical offset envelope (poindexter#745).

    ``{items, total, limit, offset}`` via ``ListResponse[VideoEpisodeItem]``.
    Replaces the prior untyped body that used ``episodes``/``count`` keys
    (``count`` is recoverable as ``len(items)``). The endpoint returns the full
    set unpaginated, so ``offset`` is always 0 and ``limit`` equals ``total``.
    Public endpoint, but the JSON list has no in-repo consumer — clients use the
    RSS ``video-feed.xml`` and the ``/episodes/{post_id}.mp4`` stream.
    """
