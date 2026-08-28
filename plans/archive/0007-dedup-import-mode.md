# Plan 0007: Dedup-import mode (`--dedup-import`)

## Problem

Merging a private photo backup (an old drive without Takeout structure or JSON
sidecars) into an already-organised degoogled library is not possible today:

- `--dedup-scan` deduplicates only **within** its `--source` folders. When the
  `--output` already contains organised photos, pre-existing destination files
  are never content-checked — the same photo from the old drive is copied
  again and renamed with a `_2` suffix (`cli.py:127`).
- `--dedup-scan` recreates the source folder tree under `by-folder/`. There is
  no way to get migration-style album symlinks keyed by directory name.

## Scope (confirmed with user)

- New dedicated flag: **`--dedup-import`** (argparse dest `dedup_import`).
  Mutually exclusive with `--dedup-scan`; both together → error.
- Files are **copied** into the existing `YYYY/MM/` structure (same date
  cascade, `YYYY/unknown/`, `needs_review/`, collision resolution, and
  sniffed-name rewrite as `--dedup-scan`).
- **Destination-aware skipping**: Phase 0 hashes all existing media files
  inside `--output` into an in-memory `existing_md5s: set[str]`. A source file
  whose MD5 is in that set is skipped as "already in destination". After each
  successful copy its MD5 is added to the set (intra-run dedup; reruns are
  resume-safe because prior-run copies are pre-skipped).
- **In-memory only**: hashes live for a single run; nothing is persisted, no
  cache files. Each run rescans the destination from scratch (user accepted;
  will evaluate with their library).
- Only **real files** in the output are hashed — symlink entries (from
  `Albums/`, `by-folder/`, or `ImportedAlbums/` of earlier runs) are excluded
  via `p.is_symlink()` checks.
- Directory-based aliases go under a **separate root `ImportedAlbums/`** (not
  `Albums/`) so they never overlap with Google Photos albums. Keyed by the
  source file's **immediate parent directory name**. No `by-folder/` tree
  mirror in import mode.
- Alias naming **reuses the migration album prefix logic exactly** (leading
  `YYYY-MM-DD ` from the oldest *precisely* dated file; existing leading
  year-first dates normalised, not double-prefixed). To share it, the private
  naming helpers in `albums.py` are extracted into a public function used by
  both modes. Files with `parent_dir` (year-only) or no dates contribute
  `None` and do not influence the prefix — same rule as migration.
- Generic album names (`Photos from YYYY`, `Untitled(N)`) are excluded, same
  as migration.
- Overlap guard: if a source root contains `--output` (or vice versa) → error,
  to prevent a source from matching itself.
- Report: dedup-style HTML gains an "Already in destination" stat and a
  per-file status bucket distinct from duplicate groups.

Non-goals: persistent hash caches, prefiltering by size/name, hash database,
source modification, two-way merging, photo-quality similarity matching.

## Changes

### `degoogle_photos/dedup.py`

- Add `hash_files(files, progress_cb=None) -> dict[Path, str]` — hash each
  file once, return a `Path -> md5` map; optional progress callback. Shared by
  the existing dedup path (as a refactor step) and import mode.
- `group_duplicates` remains for the existing `--dedup-scan` flow.

### `degoogle_photos/albums.py`

- Extract `_format_album_name(name, oldest_dt)` into a **public**
  `album_folder_name(name, oldest_dt)`; `_normalize_leading_date` and
  `_GENERIC_ALBUM_RE` stay internal to that helper. Migration mode and import
  mode both use it.
- Add `root_name: str = "Albums"` parameter to `create_album_symlinks()`;
  import mode passes `"ImportedAlbums"`. All other behavior (sanitisation,
  legacy-folder cleanup, relative symlinks) unchanged.

### `degoogle_photos/cli.py`

- New flag `--dedup-import`; error if combined with `--dedup-scan`.
- Add `_run_import(args)` orchestrator:
  - **Overlap guard** first (source ⊆ output or output ⊆ source → error).
  - **Phase 0 (reference set)**: if output exists, `find_all_media_files` on
    it, drop symlinks, `hash_files()` into `existing_md5s`.
  - **Phase 1/2**: scan sources (`find_all_media_files`), hash all files via
    `hash_files()` (progress output consistent with dedup mode).
  - **Phase 3**: per file, if its MD5 is in `existing_md5s` → report as
    "already in destination" (no copy) and **still register an album entry**
    (dest recomputed via `compute_dest_path` — collision-guess is fine for a
    symlink target). Otherwise extract date, `compute_dest_path` with
    `effective_media_name`, `resolve_collision`, `shutil.copy2`, add MD5 to
    the set, append `(dest, prefix_dt | None)` to `album_files[parent_dir]`
    (`prefix_dt` is `None` for `parent_dir` dates, like migration).
  - **Phase 4**: `create_album_symlinks(output_root, album_files, dry_run,
    log, root_name="ImportedAlbums")`.
  - Report + summary unchanged in shape; new stat row for destination skips.

### `degoogle_photos/report.py`

- `DedupReport`: add `skipped_dest` entries (`source`, `dest` guess), a
  `mode_label` for the header text, and an "Already in destination" stat in
  the summary grid. Status labels stay `COPIED` / `SKIPPED`; destination
  skips get their own section instead of fake duplicate groups.

## Tests (`tests/test_dedup_import.py`, new)

- Pre-existing dest with same MD5 → file skipped; report `skipped_dest=1`.
- Rerun the same import → all files destination-skipped (resume-safe).
- `ImportedAlbums/<parent-dir>/` links created; **no `by-folder/`** anywhere.
- Dest containing only symlinks (from a prior migration) → no crash, no
  false-positive match.
- `--dedup-import` + `--dedup-scan` together → parse error.
- Leading-date folder name (`2010-05-04 reunion`) → normalised prefix, not
  double-prefixed.
- Album name prefix identical to migration mode (shared helper invoked).
- Overlap guard: source contains output (and vice versa) → error exit.
- Dry-run: nothing copied, report written, no symlinks.
- Two files in different nesting depth → alias keyed by immediate parent only.
- needs_review file → copied to `needs_review/` + alias entry without prefix.

## Housekeeping

- This file is `plans/0007-dedup-import-mode.md`; on completion move to
  `plans/archive/` with `git mv` (folder position is the status marker).
- Update `README.md` (new "Dedup-import mode" usage + example) and
  `AGENTS.md` (mention import mode alongside dedup) when implementing.

## Verification

- `pytest -v tests/test_dedup_import.py`
- Full `pytest -v` (existing `test_dedup_mode.py` must keep passing —
  `by-folder/` behavior of `--dedup-scan` is unchanged)
- Manual smoke: `python3 -m degoogle_photos.cli --dedup-import --dry-run
  --source <old drive> --output <existing DeGoogle-Edge Photos>`
