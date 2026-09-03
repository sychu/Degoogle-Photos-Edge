"""Tests for CLI default output resolution (plan 0010).

Migration defaults to ``./DeGoogle Photos``; ``--dedup-scan`` swaps an
unchanged default to ``./Deduped Photos``; an explicit ``--output`` is never
swapped.
"""

import sys
from pathlib import Path

import pytest

import degoogle_photos.cli as cli


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Prevent tests from opening a web browser."""
    monkeypatch.setattr("webbrowser.open", lambda *a, **kw: None)


def test_migration_default_output_is_degoogle_photos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", "--source", str(tmp_path), "--dry-run"])
    cli.main()

    report_root = tmp_path / "DeGoogle Photos" / "Reports" / "DeGoogle Reports"
    run_dirs = [d for d in report_root.iterdir()
                if d.is_dir() and d.name.startswith("migration-")]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "index.html").exists()


def test_dedup_scan_swaps_default_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_20200510_120000.jpg").write_bytes(b"a")
    monkeypatch.setattr(sys, "argv", ["prog", "--dedup-scan", "--source", str(src), "--dry-run"])
    cli.main()

    assert (tmp_path / "Deduped Photos" / "Reports" / "Dedup Reports" / "index.html").exists()
    assert not (tmp_path / "DeGoogle Photos").exists()


def test_explicit_output_is_never_swapped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_20200510_120000.jpg").write_bytes(b"a")
    out = tmp_path / "custom"
    monkeypatch.setattr(
        sys, "argv",
        ["prog", "--dedup-scan", "--source", str(src), "--dry-run", "--output", str(out)],
    )
    cli.main()

    assert (out / "Reports" / "Dedup Reports" / "index.html").exists()
    assert not (tmp_path / "Deduped Photos").exists()
