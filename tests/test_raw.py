"""Tests for RAW file handling (plan 0011).

RAW camera files are matched across all modes, copied into a separate ``Raw/``
tree (``Raw/Needs Review``, ``Raw/YYYY/unknown``, ``Raw/YYYY/MM``), participate
in album/alias symlinks, and surface a dedicated RAW section in every report.
The optional exiftool fallback supplies embedded dates/metadata for formats
Pillow cannot read (videos, RAW, HEIC) via a persistent ``-stay_open`` process.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from degoogle_photos.cli import _run_dedup, _run_import, main
from degoogle_photos.copy import compute_dest_path
from degoogle_photos.dates import extract_date
import degoogle_photos.dates as dates_mod
import degoogle_photos.exiftool_util as etu
from degoogle_photos.media import RAW_EXTENSIONS, is_raw_file


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Prevent tests from opening a web browser."""
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: None)


def make_args(source, output, dry_run=False):
    sources = source if isinstance(source, list) else [source]
    return argparse.Namespace(source=sources, output=output, dry_run=dry_run)


def _run_migration(monkeypatch, source, output):
    monkeypatch.setattr(
        sys, "argv", ["pytest", "--source", str(source), "--output", str(output)]
    )
    main()


# ---------------------------------------------------------------------------
# media.py
# ---------------------------------------------------------------------------

def test_is_raw_file_case_insensitive():
    assert is_raw_file("IMG_0001.CR2")
    assert is_raw_file("photo.nef")
    assert is_raw_file(Path("photo.DNG"))
    assert not is_raw_file("photo.jpg")
    assert not is_raw_file("clip.mp4")


def test_raw_extensions_present():
    for ext in (".cr2", ".nef", ".arw", ".dng", ".raf", ".rw2"):
        assert ext in RAW_EXTENSIONS


# ---------------------------------------------------------------------------
# copy.compute_dest_path — Raw/ tree
# ---------------------------------------------------------------------------

def test_compute_dest_path_raw_dated(output_dir):
    dest = compute_dest_path(output_dir, Path("/fake/IMG_0001.cr2"), datetime(2020, 5, 10))
    assert dest == output_dir / "Raw" / "2020" / "05" / "IMG_0001.cr2"


def test_compute_dest_path_raw_year_only(output_dir):
    dest = compute_dest_path(output_dir, Path("/fake/x.NEF"), datetime(2015, 1, 1),
                             date_source="parent_dir")
    assert dest == output_dir / "Raw" / "2015" / "unknown" / "x.NEF"


def test_compute_dest_path_raw_no_date(output_dir):
    dest = compute_dest_path(output_dir, Path("/fake/x.DNG"), None)
    assert dest == output_dir / "Raw" / "Needs Review" / "x.DNG"


def test_compute_dest_path_non_raw_unchanged(output_dir):
    dest = compute_dest_path(output_dir, Path("/fake/photo.jpg"), datetime(2020, 5, 10))
    assert dest == output_dir / "2020" / "05" / "photo.jpg"


def test_compute_dest_path_dest_name_drives_classification(output_dir):
    """A sniffed dest_name (e.g. a .heic mislabeled video) is not treated as RAW."""
    # .mp4 dest_name → regular tree even though source is .heic
    dest = compute_dest_path(output_dir, Path("/fake/video.HEIC"), datetime(2020, 5, 10),
                             dest_name="video.mp4")
    assert dest == output_dir / "2020" / "05" / "video.mp4"
    # .dng dest_name → raw tree (classification follows effective name)
    dest2 = compute_dest_path(output_dir, Path("/fake/img.tiff"), datetime(2020, 5, 10),
                              dest_name="img.dng")
    assert dest2 == output_dir / "Raw" / "2020" / "05" / "img.dng"


# ---------------------------------------------------------------------------
# Migration: RAW lands in Raw/YYYY/MM/ with sidecar + album link
# ---------------------------------------------------------------------------

def _add_cr2(album_dir: Path, name: str, timestamp: int) -> None:
    (album_dir / name).write_bytes(b"\x00" * 200)
    sidecar = {
        "title": name,
        "photoTakenTime": {"timestamp": str(timestamp)},
    }
    (album_dir / f"{name}.json").write_text(json.dumps(sidecar), encoding="utf-8")


def test_migration_raw_into_raw_tree(tmp_path, monkeypatch):
    album = tmp_path / "Takeout1" / "Google Photos" / "Album1"
    album.mkdir(parents=True)
    _add_cr2(album, "IMG_0001.CR2", 1589155200)  # 2020-05-11
    output = tmp_path / "output"

    _run_migration(monkeypatch, tmp_path, output)

    bucket = output / "Raw" / "2020" / "05"
    assert (bucket / "IMG_0001.CR2").exists()
    assert (bucket / "IMG_0001.CR2.json").exists()

    # Album symlink points into the Raw/ tree (the album is date-prefixed).
    links = [p for p in (output / "Google Albums").rglob("IMG_0001.CR2") if p.is_symlink()]
    assert links, "no Google Albums symlink for the RAW file found"
    assert all("Raw" in l.resolve().parts for l in links)

    # Report index has the RAW stat + section + the Raw/ folder page
    root = output / "Reports" / "DeGoogle Reports"
    run_dirs = sorted(d.name for d in root.iterdir()
                      if d.is_dir() and d.name.startswith("migration-"))
    index = (root / run_dirs[-1] / "index.html").read_text(encoding="utf-8")
    assert "RAW Files" in index
    assert "<span class=\"label\">RAW files</span>" in index
    assert 'href="folder_Raw_2020_05.html"' in index
    assert (root / run_dirs[-1] / "folder_Raw_2020_05.html").exists()
    folder_html = (root / run_dirs[-1] / "folder_Raw_2020_05.html").read_text(encoding="utf-8")
    assert "IMG_0001.CR2" in folder_html
    assert 'class="badge badge-raw"' in folder_html


def test_migration_raw_rerun_is_resume_safe(tmp_path, monkeypatch):
    album = tmp_path / "Takeout1" / "Google Photos" / "Album1"
    album.mkdir(parents=True)
    _add_cr2(album, "IMG_0001.CR2", 1589155200)
    output = tmp_path / "output"

    _run_migration(monkeypatch, tmp_path, output)
    assert (output / "Raw" / "2020" / "05" / "IMG_0001.CR2").exists()

    # Rerun: same size → skipped via resume, file stays in place.
    _run_migration(monkeypatch, tmp_path, output)
    assert (output / "Raw" / "2020" / "05").glob("IMG_0001.CR2") is not None
    # No duplicated _2 file.
    assert not (output / "Raw" / "2020" / "05" / "IMG_0001_2.CR2").exists()


def test_migration_raw_needs_review_readme(tmp_path, monkeypatch):
    # Raw with no date anywhere → Raw/Needs Review/ + its own README.
    album = tmp_path / "Takeout1" / "Google Photos" / "Album1"
    album.mkdir(parents=True)
    (album / "IMG_0001.CR2").write_bytes(b"\x00" * 200)
    output = tmp_path / "output"

    _run_migration(monkeypatch, tmp_path, output)

    assert (output / "Raw" / "Needs Review" / "IMG_0001.CR2").exists()
    raw_readme = output / "Raw" / "Needs Review" / "README.txt"
    assert raw_readme.exists()
    assert "RAW files placed here" in raw_readme.read_text(encoding="utf-8")
    # Regular Needs Review README is still written (it holds video, not the raw).
    assert (output / "Needs Review" / "README.txt").exists()


def test_migration_raw_collision_suffix(tmp_path, monkeypatch):
    # Two different-content RAW files with the same name and month → _2 suffix.
    album1 = tmp_path / "Takeout1" / "Google Photos" / "Album1"
    album2 = tmp_path / "Takeout2" / "Google Photos" / "Album2"
    album1.mkdir(parents=True)
    album2.mkdir(parents=True)
    _add_cr2(album1, "IMG_0001.CR2", 1589155200)
    (album2 / "IMG_0001.CR2").write_bytes(b"\x01" * 300)
    (album2 / "IMG_0001.CR2.json").write_text(
        json.dumps({"title": "IMG_0001.CR2",
                    "photoTakenTime": {"timestamp": "1589155200"}}),
        encoding="utf-8",
    )
    output = tmp_path / "output"

    _run_migration(monkeypatch, tmp_path, output)

    bucket = output / "Raw" / "2020" / "05"
    names = sorted(p.name for p in bucket.glob("*.CR2"))
    assert "IMG_0001.CR2" in names
    assert "IMG_0001_2.CR2" in names


# ---------------------------------------------------------------------------
# Dedup-scan: RAW copied to Raw/, by-folder mirror, dedup, report section
# ---------------------------------------------------------------------------

def _dedup_media(output):
    return [
        p for p in output.rglob("*")
        if p.is_file() and "by-folder" not in p.parts and "Reports" not in p.parts
    ]


def _dedup_links(output):
    by = output / "by-folder"
    if not by.exists():
        return []
    return [p for p in by.rglob("*") if p.is_symlink()]


def _dedup_report(output):
    return (output / "Reports" / "Dedup Reports" / "index.html").read_text(encoding="utf-8")


def test_dedup_raw_copied_to_raw_tree_and_mirrored(tmp_path):
    src = tmp_path / "source"
    (src / "RAW").mkdir(parents=True)
    (src / "RAW" / "IMG_20200510_120000.CR2").write_bytes(b"\x00" * 200)
    out = tmp_path / "output"

    _run_dedup(make_args(src, out))

    assert (out / "Raw" / "2020" / "05" / "IMG_20200510_120000.CR2").exists()
    link = out / "by-folder" / "RAW" / "IMG_20200510_120000.CR2"
    assert link.is_symlink()
    resolved = link.resolve()
    assert "Raw" in resolved.parts
    assert resolved.exists()

    html = _dedup_report(out)
    assert "RAW Files" in html
    assert "<span class=\"label\">RAW files</span>" in html


def test_dedup_raw_groups_identical_raw(tmp_path):
    src = tmp_path / "source"
    (src / "A").mkdir(parents=True)
    (src / "B").mkdir(parents=True)
    content = b"\x00" * 200
    (src / "A" / "IMG_20200510_120000.CR2").write_bytes(content)
    (src / "B" / "IMG_20200510_120000.CR2").write_bytes(content)  # identical dupe
    out = tmp_path / "output"

    _run_dedup(make_args(src, out))

    raw_files = list((out / "Raw" / "2020" / "05").glob("*.CR2"))
    assert len(raw_files) == 1


def test_dedup_raw_collision_suffix(tmp_path):
    src = tmp_path / "source"
    (src / "A").mkdir(parents=True)
    (src / "B").mkdir(parents=True)
    (src / "A" / "IMG_20200510_120000.CR2").write_bytes(b"\x00" * 200)
    (src / "B" / "IMG_20200510_120000.CR2").write_bytes(b"\x01" * 200)
    out = tmp_path / "output"

    _run_dedup(make_args(src, out))

    bucket = out / "Raw" / "2020" / "05"
    names = sorted(p.name for p in bucket.glob("*.CR2"))
    assert "IMG_20200510_120000.CR2" in names
    assert "IMG_20200510_120000_2.CR2" in names


def test_dedup_no_raw_no_raw_section(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "IMG_20200510_120000.jpg").write_bytes(b"photo")
    out = tmp_path / "output"

    _run_dedup(make_args(src, out))

    html = _dedup_report(out)
    assert "RAW Files" not in html
    assert "<span class=\"label\">RAW files</span>" not in html


# ---------------------------------------------------------------------------
# Dedup-import: Raw/ tree participates in Phase 0 hash + aliases + report
# ---------------------------------------------------------------------------

def _import_media(output):
    return [
        p for p in output.rglob("*")
        if p.is_file() and not p.is_symlink()
        and "Imported Albums" not in p.parts
        and "Google Albums" not in p.parts
        and "by-folder" not in p.parts
        and "Reports" not in p.parts
    ]


def _import_run_html(output):
    runs = sorted(
        (d for d in (output / "Reports" / "Import Reports").iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )
    assert runs, "no import run directories written"
    return (runs[-1] / "index.html").read_text(encoding="utf-8")


def test_import_destination_skip_via_raw_tree(tmp_path):
    content = b"\x00" * 200
    out = tmp_path / "output"
    (out / "Raw" / "2020" / "05").mkdir(parents=True)
    (out / "Raw" / "2020" / "05" / "IMG_20200510_120000.CR2").write_bytes(content)

    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.CR2").write_bytes(content)

    _run_import(make_args(src, out))

    # Pre-existing file is the only real media file; nothing re-copied.
    assert len(_import_media(out)) == 1
    html = _import_run_html(out)
    assert "Already in Destination" in html


def test_import_new_raw_lands_in_raw_and_aliased(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.CR2").write_bytes(b"\x00" * 200)
    out = tmp_path / "output"

    _run_import(make_args(src, out))

    assert (out / "Raw" / "2020" / "05" / "IMG_20200510_120000.CR2").exists()
    links = [p for p in (out / "Imported Albums").rglob("IMG_20200510_120000.CR2")
             if p.is_symlink()]
    assert links, "no Imported Albums symlink for the RAW file found"
    assert all("Raw" in l.resolve().parts for l in links)
    html = _import_run_html(out)
    assert "RAW Files" in html
    assert "<span class=\"label\">RAW files</span>" in html


def test_import_no_raw_no_raw_section(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200101_120000.jpg").write_bytes(b"photo")
    out = tmp_path / "output"

    _run_import(make_args(src, out))

    html = _import_run_html(out)
    assert "RAW Files" not in html
    assert "<span class=\"label\">RAW files</span>" not in html


# ---------------------------------------------------------------------------
# exiftool fallback (mocked / graceful)
# ---------------------------------------------------------------------------

def test_date_from_exiftool_missing_binary_graceful(monkeypatch):
    monkeypatch.setattr(etu, "_available", None)
    monkeypatch.setattr(etu.shutil, "which", lambda *a, **kw: None)
    assert etu.is_available() is False
    assert etu.date_from_exiftool(Path("/tmp/x.mp4")) is None


def test_extract_date_uses_exiftool_source_label(tmp_path, monkeypatch):
    media = tmp_path / "clip_20200601_090000.mp4"
    media.write_bytes(b"\x00" * 10)
    # An embedded date that exiftool alone provides (no EXIF via Pillow).
    monkeypatch.setattr(dates_mod, "date_from_exiftool",
                        lambda p: datetime(2020, 6, 1, 9, 0, 0))
    dt, source = extract_date(media, None)
    assert source == "exiftool"
    assert dt == datetime(2020, 6, 1, 9, 0, 0)


def test_extract_date_falls_through_when_exiftool_off(tmp_path, monkeypatch):
    media = tmp_path / "IMG_20200510_204759.mp4"
    media.write_bytes(b"\x00" * 10)
    monkeypatch.setattr(dates_mod, "date_from_exiftool", lambda p: None)
    dt, source = extract_date(media, None)
    assert source == "filename"
    assert dt == datetime(2020, 5, 10)


class _FakeClient:
    def __init__(self, data):
        self.data = data

    def get_metadata(self, paths):
        assert len(paths) == 1
        return [self.data]


def test_metadata_from_exiftool_maps_tags(monkeypatch):
    data = {
        "EXIF:Make": "NIKON CORPORATION",
        "EXIF:Model": "NIKON D850",
        "Composite:ImageSize": "8256 5504",
        "EXIF:ISO": "400",
        "EXIF:FocalLength": "50",
        "EXIF:FNumber": "2.8",
        "Composite:GPSLatitude": "+48.8566",
        "Composite:GPSLongitude": "+2.3522",
    }
    fake = _FakeClient(data)
    monkeypatch.setattr(etu, "is_available", lambda: True)
    monkeypatch.setattr(etu, "_get_client", lambda: fake)

    meta = etu.metadata_from_exiftool(Path("/x/photo.cr2"))
    assert meta["camera"] == "NIKON CORPORATION NIKON D850"
    assert meta["dimensions"] == "8256\u00d75504"
    assert meta["iso"] == "ISO 400"
    assert meta["focal_length"] == "50mm"
    assert meta["aperture"] == "f/2.8"
    assert meta["gps"] == "48.8566, 2.3522"


def test_extract_date_pillow_still_wins_for_jpg(tmp_path):
    """exiftool is not consulted for Pillow-readable formats."""
    from PIL import Image
    from PIL.ExifTags import Base as ExifBase
    media = tmp_path / "exif.jpg"
    img = Image.new("RGB", (1, 1), "white")
    exif = Image.Exif()
    exif[36867] = "2021:03:15 14:30:00"
    img.save(media, exif=exif)
    dt, source = extract_date(media, None)
    assert source == "exif"
    assert dt == datetime(2021, 3, 15, 14, 30, 0)


# ---------------------------------------------------------------------------
# Integration: exiftool fallback stamps a real video (skipped if tools absent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not etu.is_available() or shutil.which("ffmpeg") is None,
                    reason="requires exiftool and ffmpeg binaries (and pyexiftool)")
def test_import_video_date_via_exiftool_integration(tmp_path):
    out = tmp_path / "output"
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)

    mp4 = src / "folderA" / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(mp4)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["exiftool", "-QuickTime:CreateDate=2020:01:15 10:00:00", str(mp4)],
        check=True, capture_output=True,
    )

    _run_import(make_args(src, out))

    # Video with no sidecar lands in a dated folder (not Needs Review).
    assert (out / "2020" / "01" / "clip.mp4").exists()
    assert not (out / "Needs Review" / "clip.mp4").exists()
    html = _import_run_html(out)
    # The Date Sources table shows the exiftool provider.
    assert "EXIF/QuickTime (exiftool)" in html
    # The file card (on the dated folder page) carries the exiftool badge and
    # the exiftool-derived dimensions tooltip.
    run_dir = next(d for d in (out / "Reports" / "Import Reports").iterdir()
                   if d.is_dir() and d.name.startswith("import-"))
    folder_html = (run_dir / "folder_2020_01.html").read_text(encoding="utf-8")
    assert "badge-exiftool" in folder_html
    assert "64\u00d764" in folder_html
