"""Tests for degoogle_photos.albums."""

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from degoogle_photos.albums import (
    create_album_symlinks,
    _GENERIC_ALBUM_RE,
    album_folder_name,
    _normalize_leading_date,
)


def _make_mock_log():
    log = MagicMock()
    log.log = MagicMock()
    return log


def test_create_symlinks(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()

    # Create a real dest file to symlink to
    dest_dir = output_root / "2020" / "05"
    dest_dir.mkdir(parents=True)
    dest_file = dest_dir / "photo.jpg"
    dest_file.write_bytes(b"jpeg")

    album_files = {"My Vacation": [dest_file]}
    log = _make_mock_log()

    create_album_symlinks(output_root, album_files, dry_run=False, log=log)

    link = output_root / "Google Albums" / "My Vacation" / "photo.jpg"
    assert link.is_symlink()
    assert link.resolve() == dest_file.resolve()


def test_skips_generic_albums(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()

    dest_dir = output_root / "2020" / "05"
    dest_dir.mkdir(parents=True)
    dest_file = dest_dir / "photo.jpg"
    dest_file.write_bytes(b"jpeg")

    album_files = {"Photos from 2020": [dest_file]}
    log = _make_mock_log()

    create_album_symlinks(output_root, album_files, dry_run=False, log=log)
    assert not (output_root / "Google Albums").exists()


def test_handles_existing_symlinks(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()

    dest_dir = output_root / "2020" / "05"
    dest_dir.mkdir(parents=True)
    dest_file = dest_dir / "photo.jpg"
    dest_file.write_bytes(b"jpeg")

    # Pre-create the symlink
    album_dir = output_root / "Google Albums" / "Trip"
    album_dir.mkdir(parents=True)
    link = album_dir / "photo.jpg"
    link.symlink_to(os.path.relpath(dest_file, album_dir))

    album_files = {"Trip": [dest_file]}
    log = _make_mock_log()

    # Should not crash — existing symlink is skipped
    create_album_symlinks(output_root, album_files, dry_run=False, log=log)
    assert link.is_symlink()


def test_generic_album_re():
    assert _GENERIC_ALBUM_RE.match("Photos from 2020")
    assert _GENERIC_ALBUM_RE.match("Photos from 2023")
    assert _GENERIC_ALBUM_RE.match("Untitled(1)")
    assert _GENERIC_ALBUM_RE.match("Untitled(42)")
    assert not _GENERIC_ALBUM_RE.match("Summer Vacation")
    assert not _GENERIC_ALBUM_RE.match("Photos from the trip")


def _make_alias_album(tmp_path, album_name, entries):
    """Make output + dest files, then build the album and return the album dir."""
    output_root = tmp_path / "output"
    output_root.mkdir(parents=True)

    dest_paths = []
    for i, (rel, dt) in enumerate(entries):
        dest_file = output_root / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(b"jpeg")
        dest_paths.append((dest_file, dt) if dt is not None else dest_file)

    log = _make_mock_log()
    create_album_symlinks(output_root, {album_name: dest_paths}, dry_run=False, log=log)
    return output_root / "Google Albums"


def test_date_prefixed_album(tmp_path):
    album_dir = _make_alias_album(
        tmp_path, "Trip",
        [("2019/07/photo.jpg", None), ("2020/05/photo2.jpg", datetime(2020, 5, 11))],
    )
    assert (album_dir / "2020-05-11 Trip").is_dir()


def test_prefix_uses_oldest_date(tmp_path):
    album_dir = _make_alias_album(
        tmp_path, "Trip",
        [("2018/01/photo.jpg", datetime(2018, 1, 3)), ("2020/05/photo2.jpg", datetime(2020, 5, 11))],
    )
    assert (album_dir / "2018-01-03 Trip").is_dir()


def test_existing_iso_prefix_wins(tmp_path):
    album_dir = _make_alias_album(
        tmp_path, "2020-05-11 Trip",
        [("2018/01/photo.jpg", datetime(2018, 1, 3))],
    )
    assert (album_dir / "2020-05-11 Trip").is_dir()
    assert not (album_dir / "2018-01-03 2020-05-11 Trip").exists()


def test_leading_date_variants_normalised(tmp_path):
    cases = ["2020.05.11 Trip", "2020_05_11 Trip", "2020/05/11 Trip", "20200511 Trip"]
    for i, name in enumerate(cases):
        album_dir = _make_alias_album(tmp_path / f"out{i}", name,
                                      [("2018/01/photo.jpg", datetime(2018, 1, 3))])
        assert (album_dir / "2020-05-11 Trip").is_dir()


def test_only_undated_items_unchanged(tmp_path):
    album_dir = _make_alias_album(
        tmp_path, "Trip",
        [("2019/07/photo.jpg", None), ("2019/08/photo2.jpg", None)],
    )
    assert (album_dir / "Trip").is_dir()


def test_normalize_leading_date():
    assert _normalize_leading_date("2020-05-11 Trip") == "2020-05-11 Trip"
    assert _normalize_leading_date("2020.05.11 Trip") == "2020-05-11 Trip"
    assert _normalize_leading_date("2020_05_11 Trip") == "2020-05-11 Trip"
    assert _normalize_leading_date("2020/05/11 Trip") == "2020-05-11 Trip"
    assert _normalize_leading_date("20200511 Trip") == "2020-05-11 Trip"
    assert _normalize_leading_date("2020-5-1 Trip") == "2020-05-01 Trip"
    assert _normalize_leading_date("2020-05-11") == "2020-05-11"
    assert _normalize_leading_date("2020-02-30 Trip") is None
    assert _normalize_leading_date("2020-13-11 Trip") is None
    assert _normalize_leading_date("11-05-2020 Trip") is None
    assert _normalize_leading_date("Trip 2020-05-11") is None
    assert _normalize_leading_date("2020-05-11Trip") is None


def test_format_album_name():
    assert album_folder_name("Trip", datetime(2020, 5, 11)) == "2020-05-11 Trip"
    assert album_folder_name("2020.05.11 Trip", datetime(2018, 1, 3)) == "2020-05-11 Trip"
    assert album_folder_name("Trip", None) == "Trip"


def test_legacy_unprefixed_folder_removed(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir(parents=True)
    dest_dir = output_root / "2020" / "05"
    dest_dir.mkdir(parents=True)
    dest_file = dest_dir / "photo.jpg"
    dest_file.write_bytes(b"jpeg")

    legacy_dir = output_root / "Google Albums" / "Trip"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "photo.jpg").symlink_to(os.path.relpath(dest_file, legacy_dir))

    log = _make_mock_log()
    create_album_symlinks(
        output_root, {"Trip": [(dest_file, datetime(2020, 5, 11))]},
        dry_run=False, log=log,
    )

    assert not legacy_dir.exists()
    new_dir = output_root / "Google Albums" / "2020-05-11 Trip"
    assert new_dir.is_dir()
    assert (new_dir / "photo.jpg").is_symlink()


def test_legacy_folder_with_real_files_left_in_place(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir(parents=True)
    dest_dir = output_root / "2020" / "05"
    dest_dir.mkdir(parents=True)
    dest_file = dest_dir / "photo.jpg"
    dest_file.write_bytes(b"jpeg")

    legacy_dir = output_root / "Google Albums" / "Trip"
    legacy_dir.mkdir(parents=True)
    real_file = legacy_dir / "real.jpg"
    real_file.write_bytes(b"real")

    log = _make_mock_log()
    create_album_symlinks(
        output_root, {"Trip": [(dest_file, datetime(2020, 5, 11))]},
        dry_run=False, log=log,
    )

    assert legacy_dir.is_dir()
    assert real_file.is_file()
    assert (output_root / "Google Albums" / "2020-05-11 Trip" / "photo.jpg").is_symlink()
