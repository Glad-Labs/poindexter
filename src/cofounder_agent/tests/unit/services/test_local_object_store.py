"""Tests for ``LocalObjectStore``, the ``storage_provider=local`` backend.

The store turns object keys into paths, so key validation is the security
boundary: an unsafe key must never reach the filesystem. The rest pins the
contract ``R2UploadService`` relies on: atomic writes, S3 prefix semantics for
listing, and ``size()`` telling "absent" apart from "couldn't ask".
"""

from __future__ import annotations

import io
import stat
from datetime import timezone
from pathlib import Path

import pytest

from poindexter.services.local_object_store import (
    _TMP_PREFIX,
    LocalObjectStore,
    UnsafeObjectKey,
)

BASE = "http://localhost:8002/site"


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "site", BASE)


def _src(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "static/posts/index.json",
        "static/posts/my-post.json",
        "images/inline/0a1b2c3d4e5f.webp",
        "images/featured/abcd1234-ef567890.webp",
        "podcast/v2/0c5a7f0e-5f4c-4b7e-9c1e-1d2f3a4b5c6d.mp3",
        "video/some-post-id.mp4",
        "image_gen/output.png",
        "affiliate-links.json",
        "fanout/2026-09-28/no-task/hero-1.png",
    ],
)
def test_path_for_accepts_every_key_shape_the_codebase_builds(store, key):
    path = store.path_for(key)
    assert path == store.root.joinpath(*key.split("/"))


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../outside.json",
        "static/../../outside.json",
        "static//posts.json",
        "static/posts/",
        ".",
        "static/./posts.json",
        "static\\posts.json",
        "static/posts\x00.json",
        f"{_TMP_PREFIX}abc",
        f"static/{_TMP_PREFIX}abc.json",
    ],
)
def test_path_for_refuses_keys_that_could_escape_or_collide(store, key):
    with pytest.raises(UnsafeObjectKey):
        store.path_for(key)


def test_path_for_refuses_non_strings(store):
    with pytest.raises(UnsafeObjectKey):
        store.path_for(None)  # type: ignore[arg-type]


def test_root_expands_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = LocalObjectStore("~/.poindexter/site", BASE)
    assert s.root == tmp_path / ".poindexter" / "site"


# ---------------------------------------------------------------------------
# URL <-> key
# ---------------------------------------------------------------------------


def test_url_for_joins_base_and_key(store):
    assert store.url_for("images/a.webp") == f"{BASE}/images/a.webp"


def test_url_for_is_empty_without_a_base(tmp_path):
    assert LocalObjectStore(tmp_path, "").url_for("images/a.webp") == ""


def test_trailing_slash_on_base_is_ignored(tmp_path):
    s = LocalObjectStore(tmp_path, BASE + "/")
    assert s.url_for("a.json") == f"{BASE}/a.json"


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"{BASE}/images/a.webp", "images/a.webp"),
        (f"{BASE}/images/a.webp?v=2", "images/a.webp"),
        (f"{BASE}/images/a.webp#frag", "images/a.webp"),
        (f"{BASE}/images/my%20image.webp", "images/my image.webp"),
        ("https://images.example.com/images/a.webp", None),
        (f"{BASE}", None),
        (f"{BASE}/", None),
        (f"{BASE}/../secret", None),
        (f"{BASE}/images/%2e%2e/%2e%2e/secret", None),
        ("http://localhost:8002/sitemap.xml", None),  # shares a prefix, not the base
        ("", None),
    ],
)
def test_key_for_url_inverts_url_for_and_rejects_everything_else(store, url, expected):
    assert store.key_for_url(url) == expected


def test_key_for_url_needs_a_base(tmp_path):
    assert LocalObjectStore(tmp_path, "").key_for_url(f"{BASE}/a.json") is None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_file_from_a_path_stores_the_bytes(store, tmp_path):
    src = _src(tmp_path, "ep.mp3", b"ID3 audio")
    size = await store.put_file(src, "podcast/v1/ep.mp3")
    dest = store.root / "podcast" / "v1" / "ep.mp3"
    assert dest.read_bytes() == b"ID3 audio"
    assert size == len(b"ID3 audio")


@pytest.mark.asyncio
async def test_put_file_from_a_stream_stores_the_bytes(store):
    size = await store.put_file(io.BytesIO(b"RIFF....WEBP"), "images/x.webp")
    assert (store.root / "images" / "x.webp").read_bytes() == b"RIFF....WEBP"
    assert size == 12


@pytest.mark.asyncio
async def test_put_file_is_world_readable(store, tmp_path):
    await store.put_file(_src(tmp_path, "a.json", b"{}"), "static/a.json")
    mode = stat.S_IMODE((store.root / "static" / "a.json").stat().st_mode)
    assert mode == 0o644


@pytest.mark.asyncio
async def test_put_file_replaces_an_existing_object(store, tmp_path):
    await store.put_file(_src(tmp_path, "v1.json", b'{"v":1}'), "static/posts/index.json")
    await store.put_file(_src(tmp_path, "v2.json", b'{"v":2}'), "static/posts/index.json")
    assert (store.root / "static/posts/index.json").read_bytes() == b'{"v":2}'


@pytest.mark.asyncio
async def test_put_file_leaves_no_temp_file_behind(store, tmp_path):
    await store.put_file(_src(tmp_path, "a.json", b"{}"), "static/a.json")
    leftovers = [p for p in (store.root / "static").iterdir() if p.name.startswith(_TMP_PREFIX)]
    assert leftovers == []


@pytest.mark.asyncio
async def test_failed_write_removes_its_temp_file_and_keeps_the_old_object(store, tmp_path):
    await store.put_file(_src(tmp_path, "old.json", b"old"), "static/a.json")
    with pytest.raises(FileNotFoundError):
        await store.put_file(tmp_path / "does-not-exist.json", "static/a.json")
    folder = store.root / "static"
    assert [p.name for p in folder.iterdir()] == ["a.json"]
    assert (folder / "a.json").read_bytes() == b"old"


@pytest.mark.asyncio
async def test_put_file_refuses_an_unsafe_key_before_touching_disk(store, tmp_path):
    with pytest.raises(UnsafeObjectKey):
        await store.put_file(_src(tmp_path, "a.json", b"{}"), "../escape.json")
    assert not (tmp_path / "escape.json").exists()
    assert not store.root.exists()


# ---------------------------------------------------------------------------
# Reads, deletes, size
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bytes_returns_none_for_a_missing_key(store):
    assert await store.get_bytes("static/nope.json") is None


@pytest.mark.asyncio
async def test_get_bytes_returns_the_stored_bytes(store, tmp_path):
    await store.put_file(_src(tmp_path, "a.json", b'{"a":1}'), "static/a.json")
    assert await store.get_bytes("static/a.json") == b'{"a":1}'


@pytest.mark.asyncio
async def test_delete_is_idempotent(store, tmp_path):
    await store.put_file(_src(tmp_path, "a.json", b"{}"), "static/a.json")
    assert await store.delete("static/a.json") is True
    assert await store.delete("static/a.json") is True
    assert not (store.root / "static" / "a.json").exists()


@pytest.mark.asyncio
async def test_size_reports_bytes_for_a_present_key(store, tmp_path):
    await store.put_file(_src(tmp_path, "ep.mp3", b"x" * 1234), "podcast/v1/ep.mp3")
    assert await store.size("podcast/v1/ep.mp3") == 1234


@pytest.mark.asyncio
async def test_size_is_none_for_a_missing_key(store):
    assert await store.size("podcast/v1/missing.mp3") is None


@pytest.mark.asyncio
async def test_size_is_none_for_a_directory(store, tmp_path):
    await store.put_file(_src(tmp_path, "ep.mp3", b"x"), "podcast/v1/ep.mp3")
    assert await store.size("podcast/v1") is None


@pytest.mark.asyncio
async def test_size_lets_a_permission_error_through(store, monkeypatch):
    """Unreadable is "couldn't ask", never "not there": a caller that read it as
    absent would re-upload, or report a file lost, on every such error."""

    def _denied(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "stat", _denied)
    with pytest.raises(PermissionError):
        await store.size("podcast/v1/ep.mp3")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.fixture
async def populated(store, tmp_path):
    for key in (
        "static/posts/index.json",
        "static/posts/alpha.json",
        "static/posts/beta.json",
        "static/feed.json",
        "images/inline/a.webp",
    ):
        await store.put_file(_src(tmp_path, "src.bin", key.encode()), key)
    return store


@pytest.mark.asyncio
async def test_list_keys_uses_s3_prefix_semantics(populated):
    assert await populated.list_keys("static/posts/") == [
        "static/posts/alpha.json",
        "static/posts/beta.json",
        "static/posts/index.json",
    ]
    # A prefix need not end at a directory boundary.
    assert await populated.list_keys("static/posts/ind") == ["static/posts/index.json"]
    assert await populated.list_keys("static/f") == ["static/feed.json"]


@pytest.mark.asyncio
async def test_list_keys_with_an_empty_prefix_lists_everything_sorted(populated):
    keys = await populated.list_keys("")
    assert keys == sorted(keys)
    assert len(keys) == 5


@pytest.mark.asyncio
async def test_list_keys_for_a_missing_prefix_is_empty(populated):
    assert await populated.list_keys("podcast/") == []


@pytest.mark.asyncio
async def test_list_keys_on_a_store_with_no_folder_yet_is_empty(store):
    assert await store.list_keys("static/") == []


@pytest.mark.asyncio
async def test_list_skips_in_flight_temp_files(populated):
    (populated.root / "static" / "posts" / f"{_TMP_PREFIX}half-written").write_bytes(b"{")
    assert f"static/posts/{_TMP_PREFIX}half-written" not in await populated.list_keys("static/")


@pytest.mark.asyncio
async def test_list_objects_matches_the_s3_shape(populated):
    [obj] = await populated.list_objects("static/feed")
    assert set(obj) == {"key", "size", "last_modified"}
    assert obj["key"] == "static/feed.json"
    assert obj["size"] == len(b"static/feed.json")
    assert obj["last_modified"].tzinfo is timezone.utc


@pytest.mark.asyncio
async def test_listing_reports_paths_relative_to_the_root(store, tmp_path):
    await store.put_file(_src(tmp_path, "a", b"a"), "a/b/c/d.json")
    assert await store.list_keys("a/") == ["a/b/c/d.json"]
