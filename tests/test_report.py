"""Tests for degoogle_photos.report."""

from datetime import datetime
from pathlib import Path

from degoogle_photos.report import (
    HtmlReport,
    _html_escape,
    _js_string,
    _slugify,
    _GENERIC_ALBUM_RE,
)


def test_html_escape():
    assert _html_escape('<script>"alert&') == '&lt;script&gt;&quot;alert&amp;'


def test_js_string():
    assert _js_string("John's 2015 photos") == "John\\'s 2015 photos"
    assert _js_string("back\\slash") == "back\\\\slash"
    assert _js_string("quote \"ok\"") == "quote &quot;ok&quot;"


def test_slugify_basic():
    assert _slugify("My Vacation 2020") == "my_vacation_2020"


def test_slugify_special_chars():
    assert _slugify("Trip: Paris/London!") == "trip_paris_london"


def test_slugify_empty():
    assert _slugify("") == "unnamed"


def test_slugify_truncates():
    long_name = "a" * 100
    assert len(_slugify(long_name)) <= 80


def test_generic_album_re():
    assert _GENERIC_ALBUM_RE.match("Photos from 2020")
    assert _GENERIC_ALBUM_RE.match("Untitled(1)")
    assert not _GENERIC_ALBUM_RE.match("My Vacation")
    assert not _GENERIC_ALBUM_RE.match("Summer 2020")


def test_add_copied_populates_folder(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    dest = Path("/out/2020/05/photo.jpg")
    src = Path("/src/photo.jpg")
    dt = datetime(2020, 5, 10, 14, 30)

    report.add_copied(dest, src, dt, "exif", "My Vacation", True, {"camera": "Nikon"})

    assert "2020/05" in report.files_by_folder
    assert len(report.files_by_folder["2020/05"]) == 1
    entry = report.files_by_folder["2020/05"][0]
    assert entry["name"] == "photo.jpg"
    assert entry["date_source"] == "exif"


def test_add_copied_populates_album(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    dest = Path("/out/2020/05/photo.jpg")
    src = Path("/src/photo.jpg")
    dt = datetime(2020, 5, 10)

    report.add_copied(dest, src, dt, "exif", "My Vacation", True)
    assert "My Vacation" in report.files_by_album
    assert len(report.files_by_album["My Vacation"]) == 1


def test_add_copied_skips_generic_album(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    dest = Path("/out/2020/05/photo.jpg")
    src = Path("/src/photo.jpg")
    dt = datetime(2020, 5, 10)

    report.add_copied(dest, src, dt, "exif", "Photos from 2020", True)
    assert len(report.files_by_album) == 0


def test_write_creates_files(output_dir):
    report = HtmlReport(output_dir, dry_run=False)
    report.total = 1
    dest = Path("/out/2020/05/photo.jpg")
    report.add_copied(dest, Path("/src/photo.jpg"), datetime(2020, 5, 10), "exif", "Album1", True)
    report._write()

    report_dir = output_dir / "Reports" / "DeGoogle Reports"
    assert (report_dir / "index.html").exists()
    assert (report_dir / "style.css").exists()
    assert (report_dir / "folder_2020_05.html").exists()


def test_render_card_has_tooltip(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    entry = {
        "name": "photo.jpg",
        "dest": "/out/photo.jpg",
        "source": "/src/photo.jpg",
        "date": "2020-05-10 14:30:00",
        "date_source": "exif",
        "album": "Vacation",
        "had_json": True,
        "is_image": True,
        "metadata": {"camera": "Nikon D850", "photoTakenTime": "2020-05-10 14:30:00 UTC"},
    }
    html = report._render_card(entry)
    assert "data-tooltip" in html
    assert "Nikon D850" in html
    assert "Finder" in html


def test_add_copied_parent_dir_folder_key(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    dest = Path("/out/2015/unknown/photo.jpg")
    src = Path("/src/photo.jpg")
    dt = datetime(2015, 1, 1)

    report.add_copied(dest, src, dt, "parent_dir", "Photos from 2015", True)

    assert "2015/unknown" in report.files_by_folder
    assert len(report.files_by_folder["2015/unknown"]) == 1
    entry = report.files_by_folder["2015/unknown"][0]
    assert entry["date_source"] == "parent_dir"


def test_index_has_attention_section(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    report.add_copied(Path("/out/Needs Review/a.jpg"), Path("/src/a.jpg"),
                      None, "none", "Album", False)
    report.add_copied(Path("/out/2015/unknown/b.jpg"), Path("/src/b.jpg"),
                      datetime(2015, 1, 1), "parent_dir", "Album", False)
    report._write()

    index = (output_dir / "Reports" / "DeGoogle Reports" / "index.html").read_text(encoding="utf-8")
    assert "Attention Needed" in index
    assert 'href="folder_Needs_Review.html"' in index
    assert 'href="folder_2015_unknown.html"' in index
    assert "No date found from any source" in index
    assert "Year known from parent folder, month unknown" in index


def test_render_card_video(output_dir):
    report = HtmlReport(output_dir, dry_run=True)
    entry = {
        "name": "clip.mp4",
        "dest": "/out/clip.mp4",
        "source": "/src/clip.mp4",
        "date": "",
        "date_source": "none",
        "album": "",
        "had_json": False,
        "is_image": False,
        "metadata": {},
    }
    html = report._render_card(entry)
    assert ".MP4" in html
    assert "vid-thumb" in html


def _run_migration_report(output_dir, dest_name, dt):
    """One migration report run: begin_run → _write → finish_run."""
    report = HtmlReport(output_dir, dry_run=False)
    report.total = 1
    if dt is None:
        dest = Path(f"/out/Needs Review/{dest_name}")
    else:
        dest = Path(f"/out/{dt:%Y/%m}/{dest_name}")
    report.add_copied(dest, Path(f"/src/{dest_name}"), dt, "exif", "Album", False)
    report.begin_run()
    report._write()
    report.finish_run()
    return report


def test_migration_runs_are_separate_and_listed(output_dir):
    _run_migration_report(output_dir, "a.jpg", datetime(2020, 5, 10))
    _run_migration_report(output_dir, "b.jpg", datetime(2020, 6, 11))

    root = output_dir / "Reports" / "DeGoogle Reports"
    run_dirs = sorted(d.name for d in root.iterdir()
                      if d.is_dir() and d.name.startswith("migration-"))
    assert len(run_dirs) == 2
    # Each run keeps its own browsable dashboard.
    for d in run_dirs:
        assert (root / d / "index.html").exists()
    # The root index lists both runs, newest first, with an open link.
    listing = (root / "index.html").read_text(encoding="utf-8")
    assert "Migration Reports" in listing
    assert "Open report" in listing
    assert listing.index(f"{run_dirs[-1]}/index.html") < listing.index(f"{run_dirs[0]}/index.html")


def test_folder_slug_spaces(output_dir):
    report = _run_migration_report(output_dir, "a.jpg", None)

    assert "Needs Review" in report.files_by_folder
    root = output_dir / "Reports" / "DeGoogle Reports"
    run_dirs = [d for d in root.iterdir()
                if d.is_dir() and d.name.startswith("migration-")]
    assert len(run_dirs) == 1
    run_index = (run_dirs[0] / "index.html").read_text(encoding="utf-8")
    # Spaces become underscores in page names (folder_Needs_Review.html).
    assert 'href="folder_Needs_Review.html"' in run_index
    assert (run_dirs[0] / "folder_Needs_Review.html").exists()
