# Plan 0011: RAW file handling in all modes

## Problem

RAW camera files (`.CR2`, `.NEF`, `.ARW`, `.DNG`, …) are not matched by
`MEDIA_EXTENSIONS` (`cli.py:34-37`), so they are ignored by every mode: not
indexed, not deduplicated, not copied, not reported. Photographers shooting
RAW+JPEG lose the RAW half of their library when migrating Takeout exports or
deduplicating backups.

## Goal / confirmed scope

- All three modes (migration, `--dedup-scan`, `--dedup-import`) match popular
  RAW extensions and process RAW files exactly like other media (date cascade,
  MD5 dedup, collision resolution, sidecars, sniffed names).
- RAW files live in a **separate detached tree** rooted at the output root,
  following the exact same date rules as regular media inside it:
  - `Raw/YYYY/MM/file` — full date known
  - `Raw/YYYY/unknown/file` — year only (parent dir)
  - `Raw/Needs Review/file` — no date found
  Regular media tree is untouched; RAW never mixes into `YYYY/MM/`.
  (User-confirmed: "inside DeGoogle Photos … raw files are detached from the
  current structure".)
- RAW files **participate in album/alias symlinks** — `Google Albums/`,
  `Imported Albums/`, `by-folder/` (user-confirmed "include"). Symlink targets
  simply point into `Raw/…`.
- Every report shows a **separate RAW section** — only when at least one RAW
  file was spotted; no RAW content → no RAW UI.
- **exiftool fallback (`pyexiftool`)**: for extensions Pillow cannot read
  (videos, RAW, HEIC, …), embedded dates and metadata are read via the
  `exiftool` system binary through pyexiftool's `-stay_open` batch mode — one
  persistent process per run, no per-file spawns. User-confirmed rationale:
  videos/RAW in `--dedup-import` have no sidecars, so without embedded dates
  they always landed in Needs Review.

Example output root after migration:

```
DeGoogle Photos/
├── 2019/07/IMG_1234.jpg (+ .json)
├── Needs Review/…                    # regular media only
├── Raw/
│   ├── 2019/07/IMG_5678.CR2 (+ .json sidecar if present)
│   ├── 2015/unknown/DSC_0001.NEF
│   ├── Needs Review/P1000234.RAF
├── Google Albums/<album> -> ../Raw/2019/07/… or ../2019/07/…
└── Reports/…
```

## Extension set

New module `degoogle_photos/media.py` (stdlib-free, no package imports) owns
RAW classification so `copy.py`, `report.py`, and `cli.py` can all import it
without cycles:

```python
RAW_EXTENSIONS = {
    # Canon
    ".crw", ".cr2", ".cr3",
    # Nikon
    ".nef", ".nrw",
    # Sony
    ".arw", ".sr2", ".srf",
    # Fujifilm
    ".raf",
    # Olympus
    ".orf",
    # Panasonic
    ".rw2",
    # Leica
    ".rwl",
    # Pentax
    ".pef", ".ptx",
    # Adobe Digital Negative (Adobe, DJI, Ricoh, Leica, …)
    ".dng",
    # Generic
    ".raw",
    # Sigma Foveon
    ".x3f",
    # Hasselblad
    ".3fr", ".fff",
    # Phase One
    ".iiq", ".cap",
    # Leaf / Mamiya
    ".mos", ".mef",
    # Kodak
    ".kdc", ".dcr", ".kc2",
    # Minolta
    ".mrw", ".mdc",
    # Epson
    ".erf",
    # Samsung / Casio
    ".srw", ".bay",
    # GoPro / ARRI / Rawzor
    ".gpr", ".ari", ".rwz",
}

def is_raw_file(name) -> bool:
    """True when `name` (a filename or Path) has a RAW extension (case-insensitive)."""
```

`is_raw_file` lowercases the suffix, so `.CR2` matches everywhere (all scan
paths already compare `suffix.lower()`).

## Changes

### 1. `degoogle_photos/media.py` (new)

- `RAW_EXTENSIONS` + `is_raw_file()` as above, with docstrings.

### 2. `degoogle_photos/cli.py`

- Import `RAW_EXTENSIONS` from `.media`; extend the config set:
  `MEDIA_EXTENSIONS = {…existing…} | RAW_EXTENSIONS` (line 34-37). This makes
  every mode pick RAW up automatically: `build_index` (migration),
  `find_all_media_files` (dedup-scan, import Phase 1), and — critically —
  import Phase 0 hashing of existing output files, so a RAW file already in
  the library is destination-skipped.
- `_run_dedup` Phase 3 loop (lines 119-141): after a successful copy (or in
  dry-run), record `report.add_raw(src, dest)` when `is_raw_file(dest.name)`
  (use the actual post-`resolve_collision` dest).
- Console summary gains a `RAW files: N` line in dedup mode (when N > 0);
  migration summary gains it via the report (see §5).

### 3. `degoogle_photos/copy.py`

- `compute_dest_path` (lines 9-24): when `is_raw_file(name)` is true, rebase
  the root to `output_root / "Raw"`; everything after that is unchanged logic:
  ```python
  if is_raw_file(name):
      output_root = output_root / "Raw"
  # existing Needs Review / YYYY/unknown / YYYY/MM branches untouched
  ```
  All three modes call this function, so the detached tree falls out for free.
  Classification is driven by `dest_name` (the sniffed/effective name), never
  by the source extension alone. Sidecar copying, collision resolution, resume
  (`is_already_copied`), and `fix_rename_resume` need no changes — they all
  operate on the computed dest.

### 4. exiftool fallback — `exiftool_util.py` (new), `dates.py`, `metadata.py`

pyexiftool drives Phil Harvey's `exiftool` binary (>= 12.15, required by the
wrapper) in `-stay_open` batch mode — a single persistent process serves the
whole run; each query is a cheap round-trip, never a new spawn. The binary is
an **optional system dependency**: if it is missing (or the import fails), the
fallback silently disables and behavior is exactly today's.

`exiftool_util.py`:

- `PILLOW_EXIF_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}` —
  extensions Pillow reliably reads EXIF from; exiftool never runs for these
  (keeps the hot path free of subprocess round-trips).
- `is_available()` — cached check: pyexiftool importable + `shutil.which("exiftool")`.
- Lazy module-level client: `ExifToolHelper` started on first use, reused for
  every call, `shutdown()` at end of run (all modes) — context-manager style.
- `date_from_exiftool(path) -> Optional[datetime]` — `get_metadata([str(path)])`,
  first hit from (priority order): `EXIF:DateTimeOriginal`,
  `EXIF:CreateDate`, `QuickTime:CreateDate`, `QuickTime:CreationDate`,
  `XMP:CreateDate`; parsed with exiftool's default `"%Y:%m:%d %H:%M:%S"`.
  Every exception swallowed → None (best-effort).
- `metadata_from_exiftool(path) -> dict` — maps into the existing tooltip keys:
  `camera` (EXIF/QuickTime `Make`+`Model`), `dimensions` (`ImageSize` → `W×H`),
  `iso`, `focal_length`, `aperture`, `gps` (`GPSLatitude`/`GPSLongitude` + refs).
  Same tolerant parsing.

`dates.py`:

- `extract_date` step 1: extension in the Pillow set → existing Pillow path;
  otherwise → `date_from_exiftool`. A hit returns `(dt, "exiftool")` — a new,
  distinct date-source label so reports show which engine produced the date.
  Cascade priority is unchanged (EXIF-family still beats JSON/filename/parent-dir).

`metadata.py`:

- Pillow pass unchanged; when it yields no EXIF-derived keys and the extension
  is outside the Pillow set, `metadata_from_exiftool` fills the gaps.

`report.py`:

- `source_labels` + the fixed key list in `_write_index` gain `"exiftool"` →
  "EXIF/QuickTime (exiftool)"; CSS adds `.badge-exiftool` (same blue family as
  `.badge-exif`).

### 5. `degoogle_photos/report.py` — HtmlReport (migration + ImportReport)

Folder keys mirror the on-disk `Raw/` tree so pages stay honest and separate:

- `add_copied` (lines 67-94): compute `is_raw = is_raw_file(dest_path.name)`;
  prefix the folder key with `Raw/` for raw files:
  - `"Raw/Needs Review"`, `"Raw/YYYY/unknown"`, `"Raw/YYYY/MM"`.
  Store `"is_raw": is_raw` on the entry dict.
- `_write_index`:
  - New stat when raw folders are non-empty: `{raw_total}` **RAW files**
    linking to the `#raw-files` section (`raw_total` = sum of entries under
    keys starting with `"Raw/"`).
  - New section `<section id="raw-files">` — "RAW Files" — one line per Raw
    folder (count + link to its folder page), same pattern as the Attention
    section. Only rendered when non-empty.
  - Attention section: `unknown_folders` (`endswith("/unknown")`, line 197)
    already catches `Raw/YYYY/unknown`. Add `Raw/Needs Review` alongside the
    existing `Needs Review` line when present.
  - Review CSS class (line 249): add `or folder == "Raw/Needs Review"`.
- `_write_folder_page` (line 281): the "no date found" blurb also applies when
  `folder == "Raw/Needs Review"` (the `endswith("/unknown")` blurb already
  covers `Raw/YYYY/unknown`).
- `_render_card` (lines 350-410): raw entries already render an extension text
  thumb (RAW ∉ `IMAGE_EXTENSIONS`). Add a `RAW` badge
  (`<span class="badge badge-raw">RAW</span>`) + `.badge-raw` CSS so raw cards
  are visually distinct.
- No structural changes needed for `ImportReport` — it inherits `add_copied`,
  folder pages, and the index section; its "New Files Imported" table lists raw
  dests automatically.

### 6. `degoogle_photos/report.py` — DedupReport (dedup-scan)

- New `self.raw_files: list` + `add_raw(source, dest)` (mirrors `add_attention`).
- `_build_index_html`: new stat `{len(raw_files)}` **RAW files** and a
  "RAW Files" section (Source → Dest table with copy buttons, same `_row`
  pattern as attention) — both only when non-empty.

### 7. `degoogle_photos/logging_util.py`

- `write_logs` (lines 125-134): split review lines with `is_raw_file` — raw
  review entries get their own README in `output_root / "Raw" / "Needs Review"`
  (same text); regular README unchanged. Migration summary (lines 96-108) adds
  a `RAW files:` row computed from `self.html` raw folder keys.

### No changes needed

`indexing.py`, `dedup.py`, `sniff.py`, `albums.py`: extension-set- or
dest-path-driven and already exception-safe for RAW. (The `dates.py` /
`metadata.py` gains are covered in §4.)

## Non-goals

- RAW dates via `rawpy`/LibRaw — rejected in favor of the pyexiftool fallback
  (broader format coverage, and it also fixes videos; user decision).
- Embedded preview/thumbnail extraction for report cards (`exiftool
  -PreviewImage -b`) — future work.
- The `exiftool` binary is not bundled, vendored, or auto-installed — it stays
  an optional system requirement (>= 12.15 per pyexiftool).
- Magic-byte sniffing of mislabeled RAW files.
- RAW-in-JPEG extraction, XMP sidecars, video RAW (`.r3d`, `.braw`).
- Migrating legacy libraries: RAW files in old outputs (never copied before)
  don't exist — no legacy `Raw/` handling needed.

## Tests (`tests/test_raw.py`, new)

- `compute_dest_path`: raw dated → `Raw/YYYY/MM/`; raw year-only →
  `Raw/YYYY/unknown/`; raw no-date → `Raw/Needs Review/`; non-raw unchanged;
  `.CR2` uppercase matched; `dest_name` override drives classification.
- Migration (`fake_takeout` + added `.CR2` + sidecar): lands in
  `Raw/YYYY/MM/` with JSON sidecar alongside; `Google Albums/` link points
  into `Raw/`; report index has RAW stat + section + `Raw/…` folder page;
  rerun skips via resume (`is_already_copied`).
- Migration no-date raw → `Raw/Needs Review/` + raw README written there.
- Dedup-scan: RAW copied to `Raw/YYYY/MM/`; `by-folder/` mirror includes it;
  MD5 dedup groups RAW with identical RAW; report RAW section + stat present.
- Dedup-import: pre-seeded `Raw/…` file in output is hashed in Phase 0 and
  source is destination-skipped; new RAW → `Raw/YYYY/MM/`;
  `Imported Albums/` includes raw; report inherits RAW section.
- No RAW files in source → **no** RAW stat/section in any report.
- Two same-named RAW files, same month → `_2` collision suffix inside
  `Raw/YYYY/MM/`.
- exiftool fallback: with a mocked client, `extract_date` on a `.mp4`/`.CR2`
  returns the exiftool date with source label `exiftool`; with availability
  mocked off, cascade falls through to JSON/filename/parent-dir unchanged.
- Missing `exiftool` binary → no subprocess usage, no crash (graceful skip).
- Integration (skipif binary absent): stamp a dummy `.mp4` with
  `exiftool -QuickTime:CreateDate` → `--dedup-import` places it in `YYYY/MM/`
  (not Needs Review) with the `exiftool` date-source badge; metadata tooltip
  carries camera/dimensions when present.

## Housekeeping

- On completion, move this file to `plans/archive/` with `git mv`.
- Update `README.md` (RAW extensions list, `Raw/` tree in the layout example,
  per-mode notes) and `AGENTS.md` (project overview: RAW handling + `Raw/`
  tree; code organization: `media.py`; report sections note).

## Verification

- `pip install -e ".[dev]"` — pulls pyexiftool (pure Python); `exiftool -ver`
  must be >= 12.15 on dev machines for fallback tests to run.
- `pytest -v` — full suite green (existing suites must be unaffected; RAW only
  adds behavior).
- `pytest -v tests/test_raw.py`
- Manual smoke per mode with a folder containing `.CR2`/`.NEF`/`.DNG`:
  - `--dry-run` migration → dests under `Raw/YYYY/MM/`, report shows RAW section.
  - `--dedup-scan --dry-run` → `Raw/` tree + `by-folder/` links into it.
  - `--dedup-import --dry-run` into an existing library containing RAW →
    destination-skip works through the `Raw/` tree.
- Manual smoke for the fallback: `--dedup-import` a folder holding a video and
  a RAW without sidecars → both land in dated folders (not Needs Review); the
  report's Date Sources table shows "EXIF/QuickTime (exiftool)" rows.
