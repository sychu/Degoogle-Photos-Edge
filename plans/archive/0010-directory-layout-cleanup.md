# Plan 0010: Directory layout cleanup + multi-run migration reports

## Goals

1. Default output directory: `DeGoogle-Edge Photos` → `DeGoogle Photos`
   (dedup-scan implicit default: `Deduped-Edge Photos` → `Deduped Photos`).
2. `Albums/` → `Google Albums/`.
3. `ImportedAlbums/` → `Imported Albums/`.
4. `needs_review/` → `Needs Review/`.
5. All reports live under a `Reports/` directory inside the output root:
   - migration → `Reports/DeGoogle Reports/`
   - dedup-import → `Reports/Import Reports/`
   - dedup-scan → `Reports/Dedup Reports/`
6. Migration mode supports multiple runs: each run gets its own timestamped
   `migration-<ts>/` directory plus a runs-listing `index.html` (the same
   pattern `ImportReport` already uses), so migrating a brand-new Takeout
   into an existing library keeps per-run reports instead of overwriting one.
7. New names only — no auto-migration of legacy directories (`Albums/`,
   `needs_review/`, `report/`, `report-import/`, `report-dedup/` are left
   untouched in old libraries and can be merged manually).

## Background

Current hardcoded names and locations:

| What | Where |
|---|---|
| Default `--output` = `DeGoogle-Edge Photos` | `cli.py:385-386, 402-403`; dedup default swap `Deduped-Edge Photos` at `cli.py:403`; `.gitignore:14-15`; README examples |
| `Albums/` root default | `albums.py:71` (`root_name: str = "Albums"`); migration calls `create_album_symlinks` without `root_name` |
| `ImportedAlbums/` | `cli.py:213 (docstring), 330 (comment), 348, 373, 397`; `albums.py:51` (docstring) |
| `needs_review/` | `copy.py:21` (`compute_dest_path`), `logging_util.py:122` (review README dir), `report.py:60` (folder key in `add_copied`) |
| Report dirs | `report.py:40` (`HtmlReport` → `report/`), `report.py:349` (`DedupReport` → `report-dedup/`), `report.py:572` (`ImportReport` → `report-import/`) |
| Migration report = single dir | `HtmlReport` writes/overwrites `report/` in place (live `maybe_write` during processing); rerunning migration on a new Takeout loses the previous run's dashboard/pages |
| Import report = per-run | `ImportReport.write()` writes `report-import/import-<ts>/…` and regenerates `report-import/index.html` as a newest-first run listing via `_write_runs_index` |
| Dedup report = timestamped files | `DedupReport.write()` writes `dedup-<ts>.html` files + `index.html` mirrors the latest run — already multi-run, just relocated |

Notes:

- `ImportReport` subclass pages currently keep their own `index.html` inside
  each run dir (back links point at the run's own dashboard) — that behavior
  carries over unchanged.
- Migration's HTML is written **incrementally** during processing
  (`MigrationLog.progress` → `html.maybe_write` → `_write()`), so the run-dir
  swap must happen before Phase 2-4 starts, not only at final write time.
- Folder slugs are built with `folder.replace("/", "_")` in several places;
  `"Needs Review"` introduces spaces, so a shared slug helper is needed
  (`folder_Needs_Review.html`, not `folder_Needs Review.html`).
- Resume logic (`is_already_copied`, `fix_rename_resume`, dedup key checks)
  works on computed dest paths, not hardcoded dir names — renaming
  `needs_review` is safe for future runs. Old-output reruns would re-copy
  files sitting in a legacy `needs_review/`; acceptable per the new-names-only
  decision.

## Changes

### 1. `cli.py` — defaults and import-mode names

- `--output` default and help text: `DeGoogle-Edge Photos` → `DeGoogle Photos`
  (lines 385-386). Dedup-scan implicit default comparison + swap:
  `Deduped-Edge Photos` → `Deduped Photos` (lines 402-403).
- `_run_import`: `root_name="ImportedAlbums"` → `"Imported Albums"` (line 348);
  summary print `Imported albums:` path (line 373); help text for
  `--dedup-import` (line 397); docstrings/comments mentioning
  `ImportedAlbums/` (lines 213, 330).
- Migration flow (main): after creating `MigrationLog`, call
  `log.html.begin_run()` so live `maybe_write` pages land in the run dir.
  Summary/report printing happens after the final write (see §4), then
  `log.html.finish_run()` closes the run and refreshes the runs listing.
  Phase numbering/comments unchanged.

### 2. `albums.py` — album root default

- `root_name: str = "Albums"` → `"Google Albums"` (line 71); docstring
  (line 51) mentions `Google Albums/` / `Imported Albums/`. No logic changes —
  the existing dated-dir reuse and legacy-removal logic operate inside
  `albums_dir` and stay as-is.

### 3. `copy.py` — needs-review destination

- `compute_dest_path`: `output_root / "needs_review"` →
  `output_root / "Needs Review"` (line 21).

### 4. `logging_util.py` — review README + per-run log

- Review README dir (line 122): `needs_review` → `Needs Review`.
- `write_logs()`: `migration_log.txt` is written **into the run dir**
  (`self.html.run_dir`) instead of the output root, so multi-run history is
  preserved; the printed paths point at the run's report index and the run's
  log file. `Needs Review/README.txt` keeps its current overwrite behavior.
- Console summary paths updated (HTML report line, output folder line).

### 5. `report.py` — new report roots + shared run machinery

`HtmlReport` (migration):

- `self.report_dir = output_root / "Reports" / "DeGoogle Reports"`.
- New attributes: `run_prefix: str = "migration"`, `run_dir: Optional[Path]`.
- `begin_run()`: sets `self.run_dir = report_dir / f"migration-<ts>"`
  (`%Y%m%d-%H%M%S-%f`, same format as import runs) and points
  `self.report_dir` at the run dir. Incremental `_write()` calls already
  `mkdir(parents=True)`, so live writes land in the run dir.
- `finish_run()`: final `_write()` is the caller's job; this restores
  `self.report_dir` to the root and calls the shared runs-listing writer.
- `_write_runs_index(title)` — generalised from `ImportReport._write_runs_index`:
  scans `report_dir` for child dirs whose name starts with
  `f"{run_prefix}-"` and contain `index.html`, sorted newest-first; parses the
  stamp after the prefix (try/except fallback, as today); title parameterized
  ("Migration Reports" / "Dedup-import Reports"). Writes root `index.html`.
- Folder slug helper `_folder_slug(folder)` =
  `folder.replace("/", "_").replace(" ", "_")`; replaces the ad-hoc
  `replace("/", "_")` calls in the stat link, attention section, folder nav,
  and `_write_folder_page`. The hardcoded `folder_needs_review.html` stat
  link uses `_folder_slug("Needs Review")`.
- `"needs_review"` folder key → `"Needs Review"` in: `add_copied` (line 60),
  the summary stat block (lines 158-161), the attention section (190-191),
  the review-CSS class check (line 213), and the `_write_folder_page` blurb
  (line 245).

`ImportReport` (dedup-import):

- `report_dir = output_root / "Reports" / "Import Reports"`;
  `run_prefix = "import"`.
- `write()` refactored onto `begin_run()` / `_write()` / runs listing
  (deletes the hand-rolled swap logic and `_write_runs_index` override;
  title "Dedup-import Reports").

`DedupReport` (dedup-scan):

- `report_dir = output_dir / "Reports" / "Dedup Reports"`. Existing
  `dedup-<ts>.html` + index-mirrors-latest layout unchanged. Docstrings
  updated (class docstring, module docstring mentions).

### 6. Tests

Grep-driven updates (`tests/`):

- `test_albums.py`: `output_root / "Albums"` → `output_root / "Google Albums"`
  (lines 37, 55, 68, 104, 177, 188, 201, 214).
- `test_copy.py`: `needs_review` → `Needs Review` (lines 37, 64, 67).
- `test_dedup_mode.py`: parts exclusions → `"Reports"` (lines 30-32);
  `needs_review` top-dir assertions → `"Needs Review"` (lines 95-99, 300, 310).
- `test_dedup_import.py`: `"ImportedAlbums"` → `"Imported Albums"`,
  `"Albums"` → `"Google Albums"`, `report`/`report-dedup`/`report-import`
  exclusions → `"Reports"`, `report-import` path assertions →
  `Reports/Import Reports` (lines 32-37, 42-53, 181, 198, 213-279, 330-468).
- `test_report.py`: `report/` paths → `Reports/DeGoogle Reports` (lines 92-94,
  138); `folder_needs_review.html` → `folder_Needs_Review.html` (line 140);
  add_copied dest path (line 132).

New tests:

- `test_report.py`:
  - `test_migration_runs_are_separate_and_listed` — two consecutive
    `HtmlReport` runs via `begin_run()`/`_write()`/`finish_run()` produce two
    `migration-<ts>/` dirs under `Reports/DeGoogle Reports/`, each with its own
    `index.html`, and the root `index.html` lists both newest-first with an
    "Open report" link.
  - `test_folder_slug_spaces` — a file with `dt=None` lands under the
    `Needs Review` folder key; index links `folder_Needs_Review.html` and that
    page exists.
- `test_cli_defaults.py` (new, or extend `test_dedup_mode.py`): argparse
  default is `cwd / "DeGoogle Photos"`; `--dedup-scan` with no `--output`
  swaps to `cwd / "Deduped Photos"`; explicit `--output` is never swapped.

### 7. Docs

- `AGENTS.md`: output-root layout paragraph — `Google Albums/`,
  `Imported Albums/`, `Needs Review/`, and report locations
  (`Reports/DeGoogle Reports/`, `Reports/Import Reports/`,
  `Reports/Dedup Reports/`); note per-run migration reports + run log.
- `README.md`: default `--output` value and examples (lines 167-196, 290);
  directory-tree example (line 177); report locations.
- `.gitignore`: `DeGoogle-Edge Photos` / `Deduped-Edge Photos` →
  `DeGoogle Photos` / `Deduped Photos`.

## Verification

- `pytest -v` — full suite green.
- New multi-run test proves two migration runs keep separate pages and the
  runs listing sorts newest-first.
- Spot-check with a `fake_takeout` dry run: default output resolves to
  `DeGoogle Photos`, dry-run report written under
  `DeGoogle Photos/Reports/DeGoogle Reports/migration-<ts>/`, summary prints
  the run index path.
- Integration spot-check: migration run followed by `--dedup-import` and
  `--dedup-scan` runs all report into their own subdirectory under `Reports/`
  and none clobber each other.
