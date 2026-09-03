# AGENTS.md

Guidance for AI coding agents working in this repository.

**Read [CONTRIBUTING.md](CONTRIBUTING.md) first.** Its setup steps, code guidelines
(style, docstrings, testing, commits), project structure, and privacy philosophy apply
to agents as well. This file only adds agent-specific context — do not repeat
CONTRIBUTING.md content here, to keep the two from drifting apart.

## Project overview

Degoogle-Photos is a Python CLI that organizes Google Takeout photo exports into clean
`YYYY/MM/` folder hierarchies. It has three operating modes:

- **Takeout migration (default):** Scans multiple `Takeout*/Google Photos/` directories,
  builds a global index, extracts the best date per file (EXIF > JSON `photoTakenTime` >
  filename > JSON `creationTime` > parent dir year), deduplicates by MD5 hash + date, copies
  media into `YYYY/MM/` folders (keeping JSON sidecars alongside, `YYYY/unknown/` when only
  the year is known, `Needs Review/` when nothing is), creates `Google Albums/`
  symlinks, and emits an HTML report. Reruns are resume-safe, including in-place
  renames of sniffed destinations from older output. Sidecar matching falls back from a file's own JSON to
  `-edited`/`(N)` variants, and Live Photo videos (MP4/MOV) inherit their same-stem still's
  sidecar. Mislabeled `.heic`-named video files are sniffed by magic bytes (`sniff.py`) and
  copied with their real `.mp4`/`.mov` name.
- **Dedup mode (`--dedup-scan`):** Scans any folder(s) recursively, computes MD5 checksums,
  keeps one file per duplicate group (shortest path wins), copies unique files into a
  date-organised `YYYY/MM/` structure, and recreates the source folder tree under
  `by-folder/` as relative symlinks. The source folders are never modified.
- **Dedup-import mode (`--dedup-import`):** Merges an unorganised backup (no Takeout/JSON)
  into an already-organised library. It hashes the existing real files in `--output`
  (symlinks excluded) into an in-memory `existing_md5s` set, skips source files whose MD5
  matches, copies new files into `YYYY/MM/` (same date cascade/collisions as dedup mode —
  a same-named file with different content is renamed `_2`, never overwritten), and
  creates directory-name aliases under a separate `Imported Albums/` root (no `by-folder/`).
  Reruns merge into an existing dated album dir with the same leaf name rather than
  creating a second dated dir.

Reports live under a `Reports/` directory inside the output root and are per-mode so they
never clobber each other: migration → `Reports/DeGoogle Reports/` (each run gets a browsable
`migration-<timestamp>/` page set plus its own `migration_log.txt`; the root `index.html`
lists all runs newest-first), dedup-scan → `Reports/Dedup Reports/`, dedup-import →
`Reports/Import Reports/` (each import run gets a browsable `import-<timestamp>/` page set;
`Reports/Import Reports/index.html` lists all runs newest-first). New names only — legacy
`Albums/`, `needs_review/`, `report/`, `report-dedup/`, and `report-import/` dirs from older
runs are left untouched and must be merged manually.

The package is pure Python stdlib plus `Pillow` and `pyexiftool`. pyexiftool drives
the `exiftool` system binary (>= 12.15, optional — absent binary degrades gracefully
to sidecar/filename/parent-dir dates) in `-stay_open` batch mode as a date/metadata
fallback for formats Pillow cannot read (videos, RAW, HEIC). Build/packaging is
managed exclusively by `pyproject.toml`. There is a thin `migrate_photos.py` wrapper
that delegates to `degoogle_photos.cli:main` for backwards compatibility.

## Repository layout

- `plans/` — implementation plans. Active plans live in `plans/NNNN-slug.md` (4-digit,
  zero-padded numbers, never reused or renumbered — check existing files to find the
  next number). Completed plans live in `plans/archive/`; move with `git mv` when done
  without editing the file — folder position is the status marker.

## Setup and verification

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

On Windows activate with `.venv\Scripts\activate` instead.

To run a single module or test:

```bash
pytest -v tests/test_dedup.py
pytest -v tests/test_dedup_mode.py -k import_name
```

## Test conventions

- Tests live in `tests/` and share fixtures from `tests/conftest.py`:
  - `fake_takeout` — builds a minimal Takeout tree (JPEG + JSON sidecar + dummy mp4 +
    `metadata.json`) for indexing/migration tests.
  - `output_dir` — a clean, empty output directory.
- `tests/test_dedup_mode.py` is the end-to-end integration suite for `--dedup-scan`.
- Keep new tests consistent with these fixtures and mirror the structure of existing test
  modules.

## Code organization

- `indexing.py` — Takeout directory scanning, JSON sidecar indexing, recursive media finder.
- `dates.py` — date extraction cascade (EXIF, JSON, filename, parent dir year).
- `metadata.py` — rich metadata extraction for report tooltips.
- `dedup.py` — MD5 hashing, dedup keys, duplicate grouping.
- `sniff.py` — magic-byte detection for mislabeled `.heic`-as-video Live Photo parts.
- `copy.py` — file copying with collision resolution and sidecar handling.
- `albums.py` — album symlink creation.
- `report.py` — HTML report generation. `HtmlReport` (migration,
  `Reports/DeGoogle Reports/` with per-run `migration-<timestamp>/` pages + a run-listing
  index), `DedupReport` (scan, `Reports/Dedup Reports/`), and `ImportReport(HtmlReport)`
  (import, `Reports/Import Reports/` with per-run `import-<timestamp>/` pages + a
  run-listing index).
- `logging_util.py` — migration logging and progress reporting.
- `cli.py` — entry point orchestrating migration and dedup-scan; defines `MEDIA_EXTENSIONS`.

## Conventions

- Write docstrings for public functions (matching CONTRIBUTING.md); inline comments are
  used for non-obvious logic (e.g. the phase markers in `cli.py`).
- `Path` objects (from `pathlib`) are used throughout instead of string paths.
- Media files are matched against `MEDIA_EXTENSIONS` in `cli.py`.
