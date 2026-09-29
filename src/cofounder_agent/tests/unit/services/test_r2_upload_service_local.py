"""``R2UploadService`` with ``storage_provider=local``.

Every public method must go to the local folder, never to boto3, and hand back
what its S3 twin would: the same keys (WebP rewrite included), the same kind of
URL, and the same failure values. The S3 path itself is covered by
``test_r2_upload_service.py``, whose tests pass unchanged because a missing
``storage_provider`` row still means S3.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from poindexter.services.r2_upload_service import (
    ObjectStoreUnavailable,
    R2UploadService,
    _webp_key,
)
from poindexter.services.site_config import SiteConfig

API = "http://localhost:8002"
BASE = f"{API}/site"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "site"


def _svc(root: Path, **extra: str) -> R2UploadService:
    values = {
        "storage_provider": "local",
        "storage_local_dir": str(root),
        "api_url": API,
        # S3 credentials present on purpose: local mode must ignore them.
        "storage_endpoint": "https://s3.example.invalid",
        "storage_bucket": "bucket",
        "storage_access_key": "AKIA-TEST",
    }
    values.update(extra)
    return R2UploadService(site_config=SiteConfig(initial_config=values))


@pytest.fixture(autouse=True)
def no_boto3():
    """Any boto3 client construction in local mode is a bug."""
    boom = MagicMock(side_effect=AssertionError("boto3 must not be used in local mode"))
    with patch("boto3.client", boom):
        yield boom


def _png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (32, 16), (40, 90, 200)).save(path, format="PNG")
    return path


# ---------------------------------------------------------------------------
# upload_to_r2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_stores_the_file_and_returns_its_site_url(root, tmp_path):
    src = tmp_path / "ep.mp3"
    src.write_bytes(b"ID3 audio")
    url = await _svc(root).upload_to_r2(str(src), "podcast/v1/ep.mp3", "audio/mpeg")
    assert url == f"{BASE}/podcast/v1/ep.mp3"
    assert (root / "podcast" / "v1" / "ep.mp3").read_bytes() == b"ID3 audio"


@pytest.mark.asyncio
async def test_png_is_converted_to_webp_under_the_same_key_s3_would_use(root, tmp_path):
    src = _png(tmp_path / "hero.png")
    url = await _svc(root).upload_to_r2(str(src), "images/inline/abc.png", "image/png")
    assert url == f"{BASE}/{_webp_key('images/inline/abc.png')}"
    stored = (root / "images" / "inline" / "abc.webp").read_bytes()
    assert stored[:4] == b"RIFF" and stored[8:12] == b"WEBP"
    assert not (root / "images" / "inline" / "abc.png").exists()


@pytest.mark.asyncio
async def test_images_use_the_custom_image_domain_when_set(root, tmp_path):
    src = _png(tmp_path / "hero.png")
    svc = _svc(root, storage_image_custom_domain="https://img.example/")
    url = await svc.upload_to_r2(str(src), "images/featured/x.png", "image/png")
    assert url == "https://img.example/images/featured/x.webp"


@pytest.mark.asyncio
async def test_objects_live_under_the_site_url_when_it_is_set(root, tmp_path):
    src = tmp_path / "a.json"
    src.write_text("{}")
    svc = _svc(root, site_url="https://blog.example")
    assert (
        await svc.upload_to_r2(str(src), "static/a.json", "application/json")
        == "https://blog.example/static/a.json"
    )


@pytest.mark.asyncio
async def test_missing_source_file_returns_none(root, tmp_path):
    assert await _svc(root).upload_to_r2(str(tmp_path / "nope.mp3"), "podcast/x.mp3") is None


@pytest.mark.asyncio
async def test_unsafe_key_is_refused_and_nothing_is_written(root, tmp_path):
    src = tmp_path / "a.json"
    src.write_text("{}")
    assert await _svc(root).upload_to_r2(str(src), "../escape.json") is None
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.asyncio
async def test_unwritable_folder_returns_none(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the folder should be")
    src = tmp_path / "a.json"
    src.write_text("{}")
    assert await _svc(blocker).upload_to_r2(str(src), "static/a.json") is None


@pytest.mark.asyncio
async def test_no_public_url_returns_none(root, tmp_path):
    src = tmp_path / "a.json"
    src.write_text("{}")
    assert await _svc(root, api_url="").upload_to_r2(str(src), "static/a.json") is None


# ---------------------------------------------------------------------------
# The rest of the contract
# ---------------------------------------------------------------------------


@pytest.fixture
async def stored(root, tmp_path) -> R2UploadService:
    svc = _svc(root)
    for key, body in {
        "static/posts/index.json": b'{"posts": [], "total": 0}',
        "static/posts/alpha.json": b'{"slug": "alpha"}',
        "podcast/feed.xml": b"<rss/>",
    }.items():
        src = tmp_path / "src.bin"
        src.write_bytes(body)
        assert await svc.upload_to_r2(str(src), key)
    return svc


def test_object_url(root):
    assert _svc(root).object_url("video/p.mp4") == f"{BASE}/video/p.mp4"


@pytest.mark.asyncio
async def test_list_keys_and_list_objects(stored):
    assert await stored.list_keys("static/posts/") == [
        "static/posts/alpha.json",
        "static/posts/index.json",
    ]
    objs = await stored.list_objects("podcast/")
    assert [o["key"] for o in objs] == ["podcast/feed.xml"]
    assert objs[0]["size"] == len(b"<rss/>")


@pytest.mark.asyncio
async def test_get_json(stored):
    assert await stored.get_json("static/posts/alpha.json") == {"slug": "alpha"}
    assert await stored.get_json("static/posts/missing.json") is None


@pytest.mark.asyncio
async def test_get_json_of_invalid_json_is_none(stored, root):
    (root / "static" / "bad.json").write_text("{not json")
    assert await stored.get_json("static/bad.json") is None


@pytest.mark.asyncio
async def test_get_object_text(stored):
    assert await stored.get_object_text("podcast/feed.xml") == "<rss/>"
    assert await stored.get_object_text("podcast/none.xml") is None


@pytest.mark.asyncio
async def test_delete_object_is_idempotent(stored, root):
    assert await stored.delete_object("static/posts/alpha.json") is True
    assert not (root / "static" / "posts" / "alpha.json").exists()
    assert await stored.delete_object("static/posts/alpha.json") is True


@pytest.mark.asyncio
async def test_object_size_tri_state(stored):
    assert await stored.object_size("podcast/feed.xml") == len(b"<rss/>")
    assert await stored.object_size("podcast/none.mp3") is None
    with pytest.raises(ObjectStoreUnavailable):
        await stored.object_size("../escape")


@pytest.mark.asyncio
async def test_object_size_unreadable_folder_is_unavailable(stored, monkeypatch):
    def _denied(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "stat", _denied)
    with pytest.raises(ObjectStoreUnavailable):
        await stored.object_size("podcast/feed.xml")


# ---------------------------------------------------------------------------
# S3 stays S3
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", [None, "", "s3"])
@pytest.mark.asyncio
async def test_s3_mode_still_goes_to_boto3(provider, tmp_path, no_boto3):
    values = {
        "storage_endpoint": "https://s3.example.invalid",
        "storage_bucket": "bucket",
        "storage_access_key": "AKIA-TEST",
        "storage_public_url": "https://pub.example",
        "storage_local_dir": str(tmp_path / "site"),
    }
    if provider is not None:
        values["storage_provider"] = provider
    cfg = SiteConfig(initial_config=values)

    async def _secret(key, default=""):
        return "SECRET" if key == "storage_secret_key" else default

    cfg.get_secret = _secret  # type: ignore[method-assign]
    s3 = MagicMock()
    no_boto3.side_effect = None
    no_boto3.return_value = s3
    src = tmp_path / "ep.mp3"
    src.write_bytes(b"x")
    url = await R2UploadService(site_config=cfg).upload_to_r2(
        str(src), "podcast/ep.mp3", "audio/mpeg"
    )
    assert url == "https://pub.example/podcast/ep.mp3"
    s3.upload_file.assert_called_once()
    assert not (tmp_path / "site").exists()


@pytest.mark.parametrize(
    "key,expected",
    [
        ("images/inline/a.png", "images/inline/a.webp"),
        ("images/featured/x-y.jpg", "images/featured/x-y.webp"),
        ("image_gen/noext", "image_gen/noext.webp"),
        ("a.b/c.jpeg", "a.b/c.webp"),
    ],
)
def test_webp_key(key, expected):
    assert _webp_key(key) == expected


@pytest.mark.asyncio
async def test_static_export_json_round_trips_through_get_json(root, tmp_path):
    """The export writes JSON through upload_to_r2; the reconciliation job reads
    it back through get_json. Both must agree on the key in local mode."""
    svc = _svc(root)
    src = tmp_path / "idx.json"
    src.write_text(json.dumps({"total": 3}))
    await svc.upload_to_r2(str(src), "static/posts/index.json", "application/json")
    assert await svc.get_json("static/posts/index.json") == {"total": 3}
