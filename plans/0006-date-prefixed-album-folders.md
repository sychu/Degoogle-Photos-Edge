# Plan 0006: Date-prefixed album folders

## Problem

Albums are created under `Albums/` with their exact Google Photos names, so
they cannot be sorted by date and it is hard to tell at a glance when an album's
photos were taken. The user wants each album folder to carry a leading
`YYYY-MM-DD` prefix derived from the **oldest dated file in that album**, and
wants existing (non-ISO) leading dates in album names normalised to
`YYYY-MM-DD`.

## Scope (confirmed with user)

- Prefix separator: `YYYY-MM-DD Name` (single space).
- Only **year-first numeric** leading dates are detected/normalised:
  `2023.05.10`, `2023-05-10`, `2023_05_10`, `2023/05/10`, `20230510`.
  (Day-first and month-first formats, and written month names, are out of scope.)
- Album already starting with a recognised date: normalise it, do **not**
  prepend the oldest-file date.
- Album with no dated files (all `needs_review`): leave the name unchanged.
- HTML report keeps the original Google Photos album names (folders only).
- Filesystem sanitisation (`/`→`-`, `:`→`-`) is applied to the final name,
  after date normalisation, exactly as today.

Non-goals: dedup mode (`--dedup-scan` has no albums); changing report album
pages/links; year-only or month-name dates.

## Changes

### `degoogle_photos/albums.py`

- Import `datetime` and `Optional`.
- Add `_LEADING_YEAR_FIRST_DATE_RE` — two patterns anchored at `^` with a
  whitespace-or-end boundary `(?=\s|$)` after the day:
  - separated: `(19|20)\d{2}[-/._]\d{1,2}[-/._]\d{1,2}`
  - compact: `(19|20)\d{2}\d{2}\d{2}`
- Add `_normalize_leading_date(name: str) -> Optional[str]`:
  - Match against the regex; validate month 1-12 and a real calendar day via
    `datetime(year, month, day)` (rejects e.g. Feb 30).
  - On a valid match return `f"{year}-{month:02d}-{day:02d}"` + the remainder
    (whitespace-stripped). Invalid/unmatched → `None`.
- Add `_format_album_name(album_name: str, oldest_dt: Optional[datetime]) -> str`:
  - `_normalize_leading_date` success → that name;
  - elif `oldest_dt` is not None → `f"{oldest_dt:%Y-%m-%d} {album_name}"`;
  - else → `album_name` unchanged.
- In `create_album_symlinks`, accept each item as either a `Path` (no date
  info — backward compatible) or a `(Path, dt)` tuple. Per album compute
  `oldest = min(dt for items if dt is not None, default=None)`, then derive
  `safe_name` from `_format_album_name(album_name, oldest)` before the existing
  sanitisation.

### `degoogle_photos/cli.py`

- Change `album_files` values from lists of `Path` to lists of `(Path, dt)`
  tuples at all five append sites (`cli.py:276,301,306,314,319`) and update the
  type comment at `cli.py:240`. `dt` is already in scope at each site (`None`
  in the `needs_review` branches).

## Tests (`tests/test_albums.py`)

- Existing tests keep passing: plain `Path` items carry no date → name
  unchanged, so `Albums/My Vacation`, `Albums/Trip`, and the generic-album skip
  all behave as before.
- New tests:
  - `Trip` + `datetime(2020, 5, 11)` → `Albums/2020-05-11 Trip`.
  - Two files with different dates → prefix uses the **oldest**.
  - `2020-05-11 Trip` + older file → name stays `2020-05-11 Trip` (no double
    prefix; leading date wins over oldest-file date).
  - `2020.05.11 Trip` → `2020-05-11 Trip`; same for `_`, `/`, and compact
    `20200511` variants.
  - Album with only undated items → name unchanged.

## Housekeeping

- This file is `plans/0006-date-prefixed-album-folders.md`; on completion move
  to `plans/archive/` with `git mv` (folder position is the status marker).

## Verification

- `pytest -v tests/test_albums.py`
- Full `pytest -v`
