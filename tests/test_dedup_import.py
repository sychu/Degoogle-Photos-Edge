"""Integration tests for --dedup-import mode."""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

from degoogle_photos.cli import _run_import
import degoogle_photos.cli as cli
from degoogle_photos.albums import album_folder_name


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Prevent tests from opening a web browser."""
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: None)


def make_args(source, output, dry_run=False):
    sources = source if isinstance(source, list) else [source]
    return argparse.Namespace(source=sources, output=output, dry_run=dry_run)


def copied_media_files(output: Path):
    """Real (non-symlink) media files, excluding aliases/ and report*/ subtrees."""
    return [
        p for p in output.rglob("*")
        if p.is_file() and not p.is_symlink()
        and "Imported Albums" not in p.parts
        and "Google Albums" not in p.parts
        and "by-folder" not in p.parts
        and "Reports" not in p.parts
    ]


def import_run_html(output: Path) -> str:
    """HTML of the latest import run page (Import Reports/import-*/index.html)."""
    runs = sorted(
        (d for d in (output / "Reports" / "Import Reports").iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )
    assert runs, "no import run directories written"
    return (runs[-1] / "index.html").read_text(encoding="utf-8")


def symlinks_in_imported_albums(output: Path):
    """All symlinks under output/Imported Albums/."""
    imported = output / "Imported Albums"
    if not imported.exists():
        return []
    return [p for p in imported.rglob("*") if p.is_symlink()]


# ---------------------------------------------------------------------------
# Destination-aware skipping
# ---------------------------------------------------------------------------

def test_existing_content_is_skipped(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"same-content")

    out = tmp_path / "output"
    (out / "2020" / "05").mkdir(parents=True)
    (out / "2020" / "05" / "IMG_20200510_120000.jpg").write_bytes(b"same-content")

    _run_import(make_args(src, out))

    # The pre-existing file is the only real media file; nothing re-copied.
    assert len(copied_media_files(out)) == 1
    assert not (out / "2020" / "05" / "IMG_20200510_120000_2.jpg").exists()
    assert not (out / "by-folder").exists()

    html = import_run_html(out)
    assert "Already in Destination" in html
    assert "IMG_20200510_120000.jpg" in html

    # The alias must point at the real pre-existing file, never a guessed path.
    links = symlinks_in_imported_albums(out)
    assert len(links) == 1
    assert links[0].resolve().exists()


def test_dest_hit_with_stale_guess_creates_no_dangling_alias(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"same-content")

    out = tmp_path / "output"
    (out / "2020" / "05").mkdir(parents=True)
    # Same content, stored under a collision-suffixed name — the naive
    # compute_dest_path guess (IMG_...jpg) does not exist in the library.
    (out / "2020" / "05" / "IMG_20200510_120000_2.jpg").write_bytes(b"same-content")

    _run_import(make_args(src, out))

    html = import_run_html(out)
    assert "Already in Destination" in html
    assert symlinks_in_imported_albums(out) == []


def test_intra_run_duplicates_reported_separately(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderB").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"dup-content")
    (src / "folderB" / "IMG_20200511_090000.jpg").write_bytes(b"dup-content")

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    # Only the first copy lands in the library; the duplicate is reported under
    # its own heading rather than inflating "Already in Destination".
    assert len(copied_media_files(out)) == 1
    html = import_run_html(out)
    assert "Intra-run Duplicates" in html

    links = symlinks_in_imported_albums(out)
    assert len(links) == 2
    assert all(link.resolve().exists() for link in links)


def test_copy_failure_registers_no_alias(tmp_path, monkeypatch):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"content")

    def boom(_a, _b):
        raise OSError("disk full")

    monkeypatch.setattr(cli.shutil, "copy2", boom)

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    html = import_run_html(out)
    assert "Errors" in html
    assert symlinks_in_imported_albums(out) == []


def test_rerun_is_resume_safe(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"content")

    out = tmp_path / "output"
    _run_import(make_args(src, out))
    assert len(copied_media_files(out)) == 1

    _run_import(make_args(src, out))
    assert len(copied_media_files(out)) == 1

    html = import_run_html(out)
    assert "Already in Destination" in html


def test_name_collision_renames_and_preserves_existing(tmp_path):
    """Unique content (new md5) with a filename already in the output must be
    renamed to _2, never overwrite the existing file."""
    out = tmp_path / "output"
    (out / "2020" / "05").mkdir(parents=True)
    existing = out / "2020" / "05" / "IMG_20200510_120000.jpg"
    existing.write_bytes(b"original-content")

    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"different-content")

    _run_import(make_args(src, out))

    # Original untouched; import landed at the _2-suffixed name.
    assert existing.read_bytes() == b"original-content"
    renamed = out / "2020" / "05" / "IMG_20200510_120000_2.jpg"
    assert renamed.read_bytes() == b"different-content"

    # The Imported Albums symlink points at the renamed file and resolves.
    links = symlinks_in_imported_albums(out)
    assert len(links) == 1
    assert links[0].name == "IMG_20200510_120000_2.jpg"
    assert links[0].resolve() == renamed.resolve()


def test_symlink_only_dest_is_not_a_false_positive(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"content")

    external = tmp_path / "external"
    external.mkdir()
    (external / "orig.jpg").write_bytes(b"content")

    out = tmp_path / "output"
    album_dir = out / "Google Albums" / "Album1"
    album_dir.mkdir(parents=True)
    (album_dir / "IMG_20200510_120000.jpg").symlink_to(
        os.path.relpath(external / "orig.jpg", album_dir)
    )

    _run_import(make_args(src, out))

    # The symlink's target has identical content, but symlinks are excluded from
    # the reference set, so the source file is still copied.
    assert len(copied_media_files(out)) == 1
    assert (out / "2020" / "05" / "IMG_20200510_120000.jpg").exists()


# ---------------------------------------------------------------------------
# Imported Albums aliases
# ---------------------------------------------------------------------------

def test_imported_albums_created_and_no_by_folder(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"aaa")

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    assert (out / "Imported Albums").is_dir()
    assert not (out / "by-folder").exists()
    links = symlinks_in_imported_albums(out)
    assert len(links) == 1


def test_alias_keyed_by_immediate_parent(tmp_path):
    src = tmp_path / "source"
    (src / "vacation" / "nested").mkdir(parents=True)
    (src / "vacation" / "a_20200510_120000.jpg").write_bytes(b"a")
    (src / "vacation" / "nested" / "b_20200601_090000.jpg").write_bytes(b"b")

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    names = {d.name for d in (out / "Imported Albums").iterdir()}
    assert any(n.endswith("vacation") for n in names)
    assert any(n.endswith("nested") for n in names)


def test_leading_date_folder_name_not_double_prefixed(tmp_path):
    src = tmp_path / "source"
    (src / "2010-05-04 reunion").mkdir(parents=True)
    (src / "2010-05-04 reunion" / "pic_20200101_000000.jpg").write_bytes(b"x")

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    assert (out / "Imported Albums" / "2010-05-04 reunion").is_dir()
    names = [d.name for d in (out / "Imported Albums").iterdir()]
    assert names == ["2010-05-04 reunion"]


def test_album_prefix_matches_migration_helper(tmp_path):
    src = tmp_path / "source"
    (src / "Trip").mkdir(parents=True)
    (src / "Trip" / "IMG_20200510_120000.jpg").write_bytes(b"x")

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    expected = album_folder_name("Trip", datetime(2020, 5, 10))
    assert (out / "Imported Albums" / expected).is_dir()


def test_needs_review_file_copied_and_aliased_without_prefix(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "no_date_img.jpg").write_bytes(b"nodata")

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    assert (out / "Needs Review" / "no_date_img.jpg").exists()
    assert (out / "Imported Albums" / "folderA").is_dir()
    assert (out / "Imported Albums" / "folderA" / "no_date_img.jpg").is_symlink()


# ---------------------------------------------------------------------------
# Overlap guard
# ---------------------------------------------------------------------------

def test_overlap_guard_source_contains_output(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out = src / "output"
    with pytest.raises(SystemExit):
        _run_import(make_args(src, out))


def test_overlap_guard_output_contains_source(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    src = out / "source"
    src.mkdir()
    with pytest.raises(SystemExit):
        _run_import(make_args(src, out))


# ---------------------------------------------------------------------------
# Flag exclusivity
# ---------------------------------------------------------------------------

def test_dedup_import_and_scan_are_mutually_exclusive(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "prog", "--dedup-import", "--dedup-scan", "--source", str(tmp_path),
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_copies_nothing_and_no_symlinks(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200510_120000.jpg").write_bytes(b"aaa")

    out = tmp_path / "output"
    _run_import(make_args(src, out, dry_run=True))

    assert copied_media_files(out) == []
    assert symlinks_in_imported_albums(out) == []
    assert (out / "Reports" / "Import Reports" / "index.html").exists()


# ---------------------------------------------------------------------------
# Cross-run behaviour (second drive, same source directory names)
# ---------------------------------------------------------------------------

def test_second_import_merges_into_existing_dated_album(tmp_path):
    # First import: "family" with a 2020-01-01 file.
    src1 = tmp_path / "drive1"
    (src1 / "family").mkdir(parents=True)
    (src1 / "family" / "IMG_20200101_120000.jpg").write_bytes(b"aaa")

    out = tmp_path / "output"
    _run_import(make_args(src1, out))

    first_dir = out / "Imported Albums" / "2020-01-01 family"
    assert first_dir.is_dir()
    first_link = first_dir / "IMG_20200101_120000.jpg"
    assert first_link.is_symlink()

    # Second import (different drive): same "family" leaf, new 2020-03-03 file.
    src2 = tmp_path / "drive2"
    (src2 / "family").mkdir(parents=True)
    (src2 / "family" / "IMG_20200303_160000.jpg").write_bytes(b"ccc")
    _run_import(make_args(src2, out))

    # The new symlink joins the existing dated dir instead of spawning a
    # second "2020-03-03 family" folder.
    expected_dir = out / "Imported Albums" / "2020-01-01 family"
    names = [d.name for d in (out / "Imported Albums").iterdir()]
    assert names == ["2020-01-01 family"]
    new_link = expected_dir / "IMG_20200303_160000.jpg"
    assert new_link.is_symlink()
    assert new_link.resolve().exists()


def test_each_import_run_keeps_its_own_report(tmp_path):
    src1 = tmp_path / "drive1"
    (src1 / "folderA").mkdir(parents=True)
    (src1 / "folderA" / "IMG_20200101_120000.jpg").write_bytes(b"aaa")

    src2 = tmp_path / "drive2"
    (src2 / "folderA").mkdir(parents=True)
    (src2 / "folderA" / "IMG_20200303_160000.jpg").write_bytes(b"ccc")

    out = tmp_path / "output"
    _run_import(make_args(src1, out))
    _run_import(make_args(src2, out))

    report_dir = out / "Reports" / "Import Reports"
    run_dirs = sorted(d.name for d in report_dir.iterdir()
                      if d.is_dir() and d.name.startswith("import-"))
    assert len(run_dirs) == 2
    # Each run has its own browsable index; the listing links both.
    for d in run_dirs:
        assert (report_dir / d / "index.html").exists()
    listing = (report_dir / "index.html").read_text(encoding="utf-8")
    assert all(f"{d}/index.html" in listing for d in run_dirs)


def test_import_does_not_touch_migration_report(tmp_path):
    # Pre-existing migration report must survive an import run.
    out = tmp_path / "output"
    migration_report = out / "Reports" / "DeGoogle Reports" / "index.html"
    migration_report.parent.mkdir(parents=True)
    migration_report.write_text("migration-dashboard", encoding="utf-8")

    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200101_120000.jpg").write_bytes(b"aaa")
    _run_import(make_args(src, out))

    assert migration_report.read_text(encoding="utf-8") == "migration-dashboard"
    assert (out / "Reports" / "Import Reports" / "index.html").exists()


def test_import_report_browsable(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200101_120000.jpg").write_bytes(b"aaa")

    out = tmp_path / "output"
    _run_import(make_args(src, out))

    run_dir = next(d for d in (out / "Reports" / "Import Reports").iterdir()
                   if d.is_dir() and d.name.startswith("import-"))
    html = (run_dir / "index.html").read_text(encoding="utf-8")
    # Index links to the date-folder page and the album page...
    assert "folder_2020_01.html" in html
    assert "album_foldera.html" in html
    # ...and those pages render a file card for the imported file.
    folder_html = (run_dir / "folder_2020_01.html").read_text(encoding="utf-8")
    assert "IMG_20200101_120000.jpg" in folder_html
    album_html = (run_dir / "album_foldera.html").read_text(encoding="utf-8")
    assert "IMG_20200101_120000.jpg" in album_html


def test_run_report_lists_imported_and_skipped_files(tmp_path):
    """The run page must make clear which files were imported and which skipped."""
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "matched_20200101_120000.jpg").write_bytes(b"aaa")
    (src / "folderA" / "skipped_20200102_090000.jpg").write_bytes(b"bbb")

    out = tmp_path / "output"
    (out / "2020" / "01").mkdir(parents=True)
    (out / "2020" / "01" / "skipped_20200102_090000.jpg").write_bytes(b"bbb")

    _run_import(make_args(src, out))

    html = import_run_html(out)
    # Matched files are listed under their own heading with source + dest.
    assert "New Files Imported" in html
    assert "matched_20200101_120000.jpg" in html
    # Skipped files stay under the skip heading.
    assert "Already in Destination" in html
    assert "skipped_20200102_090000.jpg" in html


def test_runs_listing_matches_report_style(tmp_path):
    src = tmp_path / "source"
    (src / "folderA").mkdir(parents=True)
    (src / "folderA" / "IMG_20200101_120000.jpg").write_bytes(b"aaa")

    out = tmp_path / "output"
    _run_import(make_args(src, out))
    _run_import(make_args(src, out))

    listing = (out / "Reports" / "Import Reports" / "index.html").read_text(encoding="utf-8")
    # Styled like the rest of the reports (summary grid + generated line).
    assert 'class="summary"' in listing
    assert 'class="stat-grid"' in listing
    assert "Import runs" in listing
    assert "Generated:" in listing
    # The listing references a style.css that lives next to it (not one level down).
    assert (out / "Reports" / "Import Reports" / "style.css").exists()
    # Both runs are listed, newest first.
    runs = sorted(d.name for d in (out / "Reports" / "Import Reports").iterdir()
                  if d.is_dir() and d.name.startswith("import-"))
    assert listing.index(f"{runs[-1]}/index.html") < listing.index(f"{runs[0]}/index.html")