"""CLI entry point — orchestrates the full migration pipeline."""

import argparse
import os
import shutil
import time
import webbrowser
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .indexing import find_takeout_dirs, build_index, find_json_for_media, find_all_media_files
from .dates import extract_date
from .metadata import extract_metadata
from .dedup import compute_md5, make_dedup_key, group_duplicates, hash_files
from .copy import (
    compute_dest_path,
    resolve_collision,
    is_already_copied,
    copy_with_sidecar,
    fix_rename_resume,
    sniffed_rename_old_path,
)
from .sniff import effective_media_name
from .albums import create_album_symlinks
from .logging_util import MigrationLog, ProgressBar
from .report import DedupReport, ImportReport
from .media import RAW_EXTENSIONS, is_raw_file
from .exiftool_util import shutdown as exiftool_shutdown

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp", ".tiff", ".tif",
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv", ".mpg", ".mpeg",
} | RAW_EXTENSIONS

PROGRESS_INTERVAL = 500


def _run_dedup(args):
    """
    Dedup mode: scan one or more --source folders, then copy one representative
    file per unique MD5 to --output (date-organised). Source folders are never modified.
    """
    source_roots = [p.resolve() for p in args.source]
    output_root = args.output
    dry_run = args.dry_run
    multi_source = len(source_roots) > 1

    for src in source_roots:
        if not src.is_dir():
            print(f"ERROR: --source '{src}' is not a directory.")
            raise SystemExit(1)

    report = DedupReport(output_root, dry_run)
    start = time.time()

    # Phase 1: Find all media files across all source roots
    file_to_source = {}   # Path -> source_root it came from
    all_files = []
    for src in source_roots:
        print(f"Phase 1: Scanning '{src}'...")
        found = find_all_media_files(src, MEDIA_EXTENSIONS)
        print(f"  Found {len(found)} media files")
        for f in found:
            file_to_source[f] = src
        all_files.extend(found)

    files = all_files
    print(f"  Total: {len(files)} media files across {len(source_roots)} source(s)")
    report.total = len(files)

    # Phase 2: Compute MD5s
    print(f"\nPhase 2: Computing checksums...")
    progress_interval = max(1, len(files) // 200)  # ~200 progress updates

    def _progress(current, total):
        report.scanned = current
        if current % progress_interval == 0 or current == total:
            elapsed = time.time() - start
            rate = current / elapsed if elapsed > 0 else 0
            pct = current / total * 100 if total > 0 else 0
            print(
                f"\r  {current}/{total} ({pct:.1f}%) | {rate:.0f} files/sec",
                end="", flush=True,
            )

    try:
        dup_groups = group_duplicates(files, progress_cb=_progress)
    except Exception as e:
        print(f"\nERROR during scan: {e}")
        raise SystemExit(1)

    print()  # newline after progress bar

    # Build the set of files that are duplicates (all but the keeper per group)
    skipped_paths = set()
    for _md5, group in dup_groups:
        for dupe in group[1:]:
            skipped_paths.add(dupe)
        report.add_group(_md5, group)

    dupe_file_count = len(skipped_paths)
    unique_count = len(files) - dupe_file_count
    print(f"  {len(dup_groups)} duplicate groups — {dupe_file_count} files will be skipped, "
          f"{unique_count} unique files to copy")

    # Phase 3: Copy unique files into YYYY/MM/ and mirror source tree as symlinks
    action = "Would copy" if dry_run else "Copying"
    print(f"\nPhase 3: {action} {unique_count} unique files to '{output_root}' (date-organised)...")
    unique_files = [f for f in files if f not in skipped_paths]
    copied = 0
    errors = 0
    raw_count = 0
    copy_interval = max(1, unique_count // 200)
    src_to_dest = {}  # track actual dest for symlink phase

    for i, src in enumerate(unique_files, 1):
        dt, date_source = extract_date(src, None)
        dest = compute_dest_path(
            output_root, src, dt, date_source,
            dest_name=effective_media_name(src),
        )
        try:
            if not dry_run:
                dest = resolve_collision(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            src_to_dest[src] = dest
            if is_raw_file(dest.name):
                raw_count += 1
                report.add_raw(src, dest)
            if date_source in ("parent_dir", "none"):
                report.add_attention(src, dest, date_source)
            copied += 1
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            report.add_error(src, msg)
            errors += 1

        if i % copy_interval == 0 or i == unique_count:
            pct = i / unique_count * 100 if unique_count > 0 else 0
            print(f"\r  {i}/{unique_count} ({pct:.1f}%)", end="", flush=True)

    print()  # newline after progress bar
    report.copied = copied

    # Phase 4: Recreate source folder tree under by-folder/ using symlinks
    # With multiple sources, prefix each tree with the source folder's name.
    by_folder_root = output_root / "by-folder"
    action4 = "Would create" if dry_run else "Creating"
    print(f"\nPhase 4: {action4} folder aliases under '{by_folder_root}'...")
    link_count = 0
    for src, dest in src_to_dest.items():
        src_root = file_to_source[src]
        rel = src.relative_to(src_root)
        link_path = by_folder_root / src_root.name / rel if multi_source else by_folder_root / rel
        try:
            if not dry_run:
                link_path.parent.mkdir(parents=True, exist_ok=True)
                if not link_path.exists():
                    rel_target = os.path.relpath(dest, link_path.parent)
                    link_path.symlink_to(rel_target)
            link_count += 1
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            report.add_error(src, f"symlink: {msg}")

    print(f"  {link_count} aliases created")

    # Write report
    output_root.mkdir(parents=True, exist_ok=True)
    report.write()

    elapsed = time.time() - start
    report_index = report.report_dir / "index.html"

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{prefix}Dedup Summary")
    print(f"{'='*60}")
    print(f"Files scanned:       {report.scanned}")
    print(f"Duplicate groups:    {len(dup_groups)}")
    print(f"Duplicates skipped:  {dupe_file_count}")
    print(f"Unique files copied: {copied}")
    print(f"Folder aliases:      {link_count}")
    if raw_count:
        print(f"RAW files:           {raw_count}")
    if errors:
        print(f"Errors:              {errors}")
    print(f"Time elapsed:        {elapsed:.1f}s")
    print(f"{'='*60}")
    for src in source_roots:
        label = f"Source ({src.name}):" if multi_source else "Source:      "
        print(f"{label} {src}")
    print(f"\nDate folders: {output_root.resolve()}")
    print(f"By folder:    {by_folder_root.resolve()}")
    print(f"Report:       {report_index.resolve()}")
    if report_index.exists():
        webbrowser.open(report_index.resolve().as_uri())
    exiftool_shutdown()


def _is_within(path: Path, parent: Path) -> bool:
    """Return True if `path` is `parent` itself or a descendant of `parent`."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _run_import(args):
    """
    Dedup-import mode: merge an unorganized backup (no Takeout/JSON structure)
    into an already-organised library, skipping files whose content already
    exists in --output. Unlike --dedup-scan, the output is treated as the
    reference set and directory-based aliases go under Imported Albums/ instead
    of a by-folder/ mirror.
    """
    source_roots = [p.resolve() for p in args.source]
    output_root = args.output.resolve()
    dry_run = args.dry_run

    for src in source_roots:
        if not src.is_dir():
            print(f"ERROR: --source '{src}' is not a directory.")
            raise SystemExit(1)

    # Overlap guard: a source cannot contain or be contained by the output,
    # otherwise it would match itself during the destination-aware skip.
    for src in source_roots:
        if _is_within(src, output_root) or _is_within(output_root, src):
            print(f"ERROR: --source '{src}' and --output '{output_root}' overlap; "
                  f"the source would match itself.")
            raise SystemExit(1)

    report = ImportReport(output_root, dry_run)
    start = time.time()

    # Phase 0: hash existing (real) files in the output into an in-memory set.
    # Symlinks (Google Albums/, by-folder/, Imported Albums/) are excluded so
    # they never count as a pre-existing copy of a source file.
    existing_md5s = set()
    if output_root.is_dir():
        print("Phase 0: Hashing existing destination files...")
        existing_files = [
            f for f in find_all_media_files(output_root, MEDIA_EXTENSIONS)
            if not f.is_symlink()
        ]
        if existing_files:
            bar = ProgressBar(max(1, len(existing_files) // 200))
            try:
                existing_md5s = set(
                    hash_files(existing_files, progress_cb=bar.update).values()
                )
            except Exception as e:
                print(f"\nERROR hashing destination: {e}")
                raise SystemExit(1)
            bar.finish()
        print(f"  {len(existing_md5s)} existing media files hashed")

    # Phase 1: Find all media files across all source roots
    all_files = []
    for src in source_roots:
        print(f"Phase 1: Scanning '{src}'...")
        found = find_all_media_files(src, MEDIA_EXTENSIONS)
        print(f"  Found {len(found)} media files")
        all_files.extend(found)

    print(f"  Total: {len(all_files)} media files across {len(source_roots)} source(s)")
    report.total = len(all_files)

    # Phase 2: Compute MD5s
    print(f"\nPhase 2: Computing checksums...")
    bar = ProgressBar(
        max(1, len(all_files) // 200),
        on_update=lambda c, t: setattr(report, "scanned", c),
    )

    try:
        file_md5 = hash_files(all_files, progress_cb=bar.update)
    except Exception as e:
        print(f"\nERROR during scan: {e}")
        raise SystemExit(1)
    bar.finish()

    # Phase 3: Copy new files and skip ones already in the destination.
    # Directory-based aliases are keyed by the source's immediate parent dir.
    action = "Would copy" if dry_run else "Copying"
    print(f"\nPhase 3: {action} new files to '{output_root}' (date-organised)...")
    album_files = defaultdict(list)  # parent_dir_name -> [(dest, prefix_dt|None), ...]
    copied = 0
    skipped_dest = 0
    skipped_intra = 0
    errors = 0
    run_md5_to_dest = {}  # md5 -> dest of files copied during this run
    copy_bar = ProgressBar(
        max(1, len(all_files) // 200),
        stats=lambda: f"copied={copied} skip-dest={skipped_dest} "
                      f"skip-intra={skipped_intra} errors={errors}",
    )

    for i, src in enumerate(all_files, 1):
        dt, date_source = extract_date(src, None)
        prefix_dt = dt if date_source != "parent_dir" else None
        dest = compute_dest_path(
            output_root, src, dt, date_source,
            dest_name=effective_media_name(src),
        )
        md5 = file_md5[src]

        known_in_run = run_md5_to_dest.get(md5)
        if known_in_run is not None:
            # Intra-run duplicate: copied earlier in this run, dest is unambiguous.
            report.add_skipped_intra(src, known_in_run)
            skipped_intra += 1
            album_files[src.parent.name].append((known_in_run, prefix_dt))
        elif md5 in existing_md5s:
            # Was in the destination before this run started. Register the alias
            # only when the collision-guess dest resolves, to avoid dangling
            # links against moved/renamed libraries (dry runs never check).
            report.add_skipped_dest(src, dest)
            skipped_dest += 1
            if dry_run or dest.exists():
                album_files[src.parent.name].append((dest, prefix_dt))
        else:
            try:
                if not dry_run:
                    dest = resolve_collision(dest)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                existing_md5s.add(md5)
                # Register the alias only on the success path — failed copies
                # must not leave a dangling Imported Albums/ entry behind.
                album_files[src.parent.name].append((dest, prefix_dt))
                report.add_copied(dest, src, dt, date_source, src.parent.name,
                                  False, metadata=extract_metadata(src, None))
                copied += 1
                run_md5_to_dest[md5] = dest
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                report.add_error(src, msg)
                errors += 1

        copy_bar.update(i, len(all_files))

    copy_bar.finish()
    report.copied = copied

    # Phase 4: Create Imported Albums/<parent-dir>/ symlinks (no by-folder/ mirror)
    create_album_symlinks(output_root, album_files, dry_run, log=MigrationLog(output_root, dry_run),
                          root_name="Imported Albums", phase="Phase 4")

    # Write report
    output_root.mkdir(parents=True, exist_ok=True)
    report.write()

    elapsed = time.time() - start
    report_index = report.report_dir / "index.html"

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{prefix}Dedup-import Summary")
    print(f"{'='*60}")
    print(f"Files scanned:           {report.scanned}")
    print(f"Already in destination:  {skipped_dest}")
    if skipped_intra:
        print(f"Intra-run duplicates:    {skipped_intra}")
    print(f"New files copied:        {copied}")
    if errors:
        print(f"Errors:                  {errors}")
    print(f"Time elapsed:            {elapsed:.1f}s")
    print(f"{'='*60}")
    for src in source_roots:
        print(f"Source: {src}")
    print(f"\nDate folders:     {output_root}")
    print(f"Imported albums:  {output_root / 'Imported Albums'}")
    print(f"Report:           {report_index} (this run: {report.run_dir.name}/index.html)")
    if report_index.exists():
        webbrowser.open(report_index.resolve().as_uri())
    exiftool_shutdown()


def main():
    parser = argparse.ArgumentParser(description="Migrate Google Takeout photos to YYYY/MM/ structure")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be done without copying or deleting")
    parser.add_argument("--source", type=Path, nargs="+", default=[Path.cwd()],
                        help="One or more source folders. For migration: root containing Takeout dirs. "
                             "For --dedup-scan: any folders to scan (repeat --source or space-separate).")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "DeGoogle Photos",
                        help="Output root for organized photos or dedup report (default: ./DeGoogle Photos)")

    # Dedup modes (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dedup-scan", action="store_true",
                            help="Copy deduplicated media files from --source to --output. "
                                 "One file is kept per duplicate group (shortest path wins). "
                                 "The source folder is never modified.")
    mode_group.add_argument("--dedup-import", action="store_true",
                            help="Merge an unorganized backup into an existing organised "
                                 "library. Files whose content already exists in --output are "
                                 "skipped; directory-based aliases go under Imported Albums/.")

    args = parser.parse_args()

    if args.dedup_scan:
        if args.output == Path.cwd() / "DeGoogle Photos":
            args.output = Path.cwd() / "Deduped Photos"
        _run_dedup(args)
        return

    if args.dedup_import:
        _run_import(args)
        return

    source_root = args.source[0]
    output_root = args.output
    dry_run = args.dry_run

    log = MigrationLog(output_root, dry_run, progress_interval=PROGRESS_INTERVAL)
    # Open the per-run report dir so live progress writes land in migration-<ts>/
    log.html.begin_run()

    # Phase 1: Build global index
    print("Phase 1: Scanning Takeout directories...")
    takeout_dirs = find_takeout_dirs(source_root)
    print(f"  Found {len(takeout_dirs)} Takeout/Google Photos directories")

    media_files, json_index = build_index(takeout_dirs, MEDIA_EXTENSIONS)
    total_jsons = sum(len(v) for v in json_index.values())
    print(f"  Found {len(media_files)} media files")
    print(f"  Indexed {total_jsons} JSON sidecars across {len(json_index)} albums")

    log.total = len(media_files)
    log.html.total = len(media_files)

    # Album tracking: album_name -> [(dest_path, dt), ...]; dt is None for
    # Needs Review and year-only (parent-dir) dates, too imprecise for a prefix.
    album_files: dict[str, list[tuple[Path, Optional[datetime]]]] = defaultdict(list)

    # Phase 2-4: Process each media file
    print(f"\nPhase 2-4: Processing files{' (dry run)' if dry_run else ''}...")
    seen_dedup_keys = set()

    for i, (media_path, album_name) in enumerate(media_files, 1):
        try:
            # Find matching JSON
            json_path = find_json_for_media(media_path, album_name, json_index)

            # Extract date
            dt, date_source = extract_date(media_path, json_path)

            # Year-only dates (parent dir) are too imprecise to prefix an album
            # name, so they only contribute when a full date is known.
            prefix_dt = dt if date_source != "parent_dir" else None

            # Extract rich metadata for report tooltips
            metadata = extract_metadata(media_path, json_path)

            # Compute destination (mislabeled .heic videos get their real name)
            dest_path = compute_dest_path(
                output_root, media_path, dt, date_source,
                dest_name=effective_media_name(media_path),
            )

            # Check resumability (rename sniffed dest before checking so a
            # rerun after the sniff reshape does not duplicate old output).
            # In dry-run, detect the same resume without touching the filesystem.
            if dry_run:
                resume_dest = sniffed_rename_old_path(media_path, dest_path) or dest_path
            else:
                fix_rename_resume(media_path, dest_path)
                resume_dest = dest_path
            if is_already_copied(media_path, resume_dest):
                log.skipped_resume += 1
                log.log(f"SKIP_RESUME: {media_path} -> {dest_path}")
                log.html.add_copied(dest_path, media_path, dt, date_source,
                                    album_name, json_path is not None, metadata,
                                    status="resumed")
                album_files[album_name].append((dest_path, prefix_dt))
                log.progress(i, log.total)
                continue

            # Deduplication
            md5 = compute_md5(media_path)
            dedup_key = make_dedup_key(md5, dt)

            if dedup_key in seen_dedup_keys:
                log.skipped_dupes += 1
                log.log(f"SKIP_DUPE: {media_path} (md5={md5})")
                log.html.add_duplicate(media_path, md5)
                log.progress(i, log.total)
                continue
            seen_dedup_keys.add(dedup_key)

            # Handle Needs Review
            if dt is None:
                log.needs_review += 1
                log.log_review(media_path, "No date found from any source")
                if not dry_run:
                    actual_dest = copy_with_sidecar(media_path, json_path, dest_path, dry_run)
                    log.log(f"REVIEW: {media_path} -> {actual_dest}")
                    log.html.add_copied(actual_dest, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata,
                                        status="review")
                    album_files[album_name].append((actual_dest, prefix_dt))
                else:
                    log.log(f"REVIEW: {media_path} -> {dest_path}")
                    log.html.add_copied(dest_path, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata,
                                        status="review")
                    album_files[album_name].append((dest_path, prefix_dt))
            else:
                # Normal copy
                if not dry_run:
                    actual_dest = copy_with_sidecar(media_path, json_path, dest_path, dry_run)
                    log.log(f"COPY: {media_path} -> {actual_dest} (date={dt})")
                    log.html.add_copied(actual_dest, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata)
                    album_files[album_name].append((actual_dest, prefix_dt))
                else:
                    log.log(f"COPY: {media_path} -> {dest_path} (date={dt})")
                    log.html.add_copied(dest_path, media_path, dt, date_source,
                                        album_name, json_path is not None, metadata)
                    album_files[album_name].append((dest_path, prefix_dt))
                log.copied += 1

        except Exception as e:
            log.errors += 1
            log.log(f"ERROR: {media_path} -- {type(e).__name__}: {e}")
            log.html.add_error(media_path, f"{type(e).__name__}: {e}")

        log.progress(i, log.total)

    # Phase 5: Create album symlinks
    print()  # newline after progress bar
    create_album_symlinks(output_root, album_files, dry_run, log)

    # Phase 6: Write reports
    log.write_logs()
    log.html.finish_run()
    exiftool_shutdown()


if __name__ == "__main__":
    main()
