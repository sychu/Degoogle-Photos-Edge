# Plan 0008: Import report structure + no-overwrite guarantee

## Warning require validation out of quota at the end of session. Review required!
## After run few obserwations:
1. imported report main page is ugly and do not match current raport style
2. raport is not clear enough. Which files were matched and which files were skipped?

## Goals

1. Guarantee that a same-named but unique-content file imported into the output is
   renamed (`_2`, `_3`, …), never overwriting an existing file — and add the test
   coverage that pins this for `--dedup-import`.
2. Align the dedup-import report with the migration report's browsable structure
   (date-folder and album links in the index, file-card pages), and stop dedup
   modes from clobbering the migration report at `report/index.html`.

## Background

- Both `HtmlReport` (migration) and `DedupReport` (scan + import) write to
  `<output>/report/index.html` — last writer wins. A dedup-import run after a
  migration overwrites the migration dashboard.
- `DedupReport` is a single flat page; it records no copied files, so nothing is
  browsable. Migration's `HtmlReport` builds `files_by_folder` / `files_by_album`
  maps and renders `folder_<slug>.html` / `album_<slug>.html` pages via
  `_render_card`.
- `_run_import` Phase 3 already calls `resolve_collision(dest)` before copying,
  so same-name/different-content files are renamed rather than overwritten — but
  `resolve_collision` uses `exists()`, which follows symlinks: a broken symlink at
  dest would be treated as free and `copy2` would write through it.
- `DedupReport` errors use key `"path"`; `HtmlReport` errors use `"source"` —
  no change required, they stay separate classes.

## Changes

### 1. `copy.py` — collision hardening

`resolve_collision` treats broken symlinks as occupied:

- Guard: `if not dest_path.exists() and not dest_path.is_symlink(): return dest_path`
- Loop: `if not candidate.exists() and not candidate.is_symlink(): return candidate`
- Docstring updated to note symlink-safety.

### 2. `report.py` — per-mode report dirs + `ImportReport(HtmlReport)`

- `DedupReport.__init__`: `self.report_dir = output_dir / "report-dedup"` (was
  `"report"`). Single-page layout unchanged; its `dedup-<ts>.html` run files move
  with it.
- New `class ImportReport(HtmlReport)`:
  - `report_dir = output_root / "report-import"`.
  - Extra state: `skipped_dest`, `skipped_intra` lists + `add_skipped_dest` /
    `add_skipped_intra` (same `{"source", "dest"}` shapes as `DedupReport`).
  - Overrides `_write_index`: calls the base content sections, then appends the
    two skip-table sections ("Already in Destination", "Intra-run Duplicates")
    reusing the `_skip_table`-style rendering.
  - `write()`: writes the run as timestamped `import-<ts>.html` content plus
    folder/album pages, and regenerates `index.html` as a listing of all run
    files (newest-first) that links into each run's page.
- `report/index.html` becomes migration-only — never touched by either dedup mode.

### 3. `cli.py` — `_run_import` wiring

- Instantiate `ImportReport(output_root, dry_run)` instead of `DedupReport`.
- Phase 3 success path: after `copied += 1`, call
  `report.add_copied(dest, src, dt, date_source, src.parent.name, False,
  metadata=extract_metadata(src, None))` — passing the **already
  collision-resolved** `dest`, so cards and links show the real `…_2.jpg` name.
  Album key = source's immediate parent dir, matching `album_files`.
- Skip/error/attention calls move to the subclass slots unchanged.
- Final print + browser-open points at `report-import/index.html`.
- `_run_dedup` unchanged apart from the automatic new `report-dedup/` location.

### 4. Tests

- `tests/test_dedup_import.py`:
  - `test_name_collision_renames_and_preserves_existing` — output pre-seeded
    with `2020/05/IMG_20200510_120000.jpg` (content A); source has a same-name
    file with different content (unique md5). After import: both files exist,
    import lands at `…_2.jpg`, original bytes unchanged, and the
    `ImportedAlbums` symlink resolves to the `_2` path.
  - `test_report_dir_isolation` — a pre-existing `report/index.html` is
    untouched after import; `report-import/index.html` is created.
  - `test_import_report_browsable` — index links to `folder_*.html` /
    `album_*.html`; those pages exist and contain file cards.
  - Update existing tests that assert on `report/` paths → `report-import/`.
- `tests/test_dedup_mode.py`: update `report/` assertions → `report-dedup/`.
- `tests/test_copy.py`:
  `test_resolve_collision_broken_symlink_treated_as_occupied` — dest is a broken
  symlink; `resolve_collision` returns the `…_2` path.

### 5. Docs

- `AGENTS.md`: report dirs are `report/` (migration), `report-dedup/` (scan),
  `report-import/` (import); note `resolve_collision` symlink hardening.
- `README.md`: update the import report location/description.

## Verification

- New collision test proves rename-not-overwrite end-to-end for import.
- Full `pytest -v` green.
- Spot-check: migration run followed by import run leaves `report/index.html`
  intact and produces `report-import/` with browsable pages.
