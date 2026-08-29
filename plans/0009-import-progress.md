# Plan 0009: Live progress reporting for --dedup-import

## Goals

Give `--dedup-import` live progress matching the migration mode's concept —
`current/total (pct%) | rate files/sec | counters` — on every slow phase.
Today Phase 0 (hashing the existing destination library) prints nothing while
it runs, and Phase 3 (copy/skip) shows percent only, so a large import looks
stuck for minutes with no feedback.

## Background

- Migration bar: `MigrationLog.progress()` in `logging_util.py` prints
  `Progress: i/total (pct%) | N files/sec | copied= dupes= review= errors=`.
- Import gaps:
  - Phase 0 — `hash_files(existing_files)` at `cli.py:248` is called with no
    `progress_cb`, so hashing a large library is silent.
  - Phase 2 — already shows `current/total (pct%) | files/sec` via an inline
    closure.
  - Phase 3 — `cli.py:341-343` prints `current/total (pct%)` only.
- The inline progress closure is duplicated in `_run_dedup` and `_run_import`
  (`cli.py:79-88` and `cli.py:269-278`).
- `report.scanned` is only updated via the Phase 2 closure (`cli.py:270`), so
  any refactor must preserve that side effect (it feeds the summary and the
  HTML report's "Files scanned" stat).

## Changes

### 1. `logging_util.py` — new `ProgressBar` class

- `__init__(self, tick: int, prefix: str = "  ", stats=None, on_update=None)`:
  - `tick` — print interval in items.
  - `prefix` — leading text before the counter (default two-space indent,
    matching current dedup output).
  - `stats` — optional callable returning the counter string, evaluated fresh
    at each print.
  - `on_update(current, total)` — optional hook called on every update so
    callers can keep side effects (e.g. `report.scanned`) in sync.
  - Records `self._start = time.time()`.
- `update(self, current, total)`:
  - Calls `on_update` if set.
  - Prints on `current % tick == 0` and always when `current == total`:
    `\r{prefix}{current}/{total} ({pct:.1f}%) | {rate:.0f} files/sec`, plus
    ` | {stats()}` when `stats` is set, `end=""`, `flush=True`.
  - Guards `total == 0` → 0% and 0 files/sec; `elapsed == 0` → 0 files/sec.
- `finish(self)` — prints the trailing newline to close the carriage-return bar.

### 2. `cli.py` — wire `_run_import`

- Phase 0: build the existing-file list, then
  `bar = ProgressBar(max(1, len(existing_files) // 200))` and pass
  `progress_cb=bar.update` into `hash_files(existing_files)`. Call
  `bar.finish()` before the existing "… existing media files hashed" line.
- Phase 2: replace the inline `_progress` closure with a `ProgressBar` whose
  `on_update` sets `report.scanned`; keep `max(1, len(all_files) // 200)`.
- Phase 3: `ProgressBar(max(1, len(all_files) // 200), stats=...)` where the
  stats callable returns
  `f"copied={copied} skip-dest={skipped_dest} skip-intra={skipped_intra} errors={errors}"`,
  replacing the manual `%`-only print. `bar.finish()` after the loop.

### 3. Tests

- New `tests/test_logging_util.py` (or extend `test_dedup_import.py`) with
  `capsys` unit tests for `ProgressBar`:
  - Prints exactly at tick multiples and always at `total`.
  - Output contains `%` and `files/sec`.
  - `stats` callable output is appended when provided.
  - `finish()` emits a newline.
  - `update(0, 0)` does not divide by zero.
- Existing import integration tests remain green (progress goes to unasserted
  stdout).

## Out of scope

- Applying the helper to `--dedup-scan` (kept import-only for now).
- Caching the Phase 0 library hash so reruns do not re-hash everything — the
  underlying cause of slow imports, tracked separately.

## Verification

- `pytest -v` green.
- Manual `--dedup-import` run: Phase 0 shows live speed, Phase 3 shows speed
  plus live `copied` / `skip-dest` / `skip-intra` / `errors` counters.
