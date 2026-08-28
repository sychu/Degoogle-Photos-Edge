"""Album symlink creation."""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .logging_util import MigrationLog

# Generic album names that Google auto-creates — not real user albums
_GENERIC_ALBUM_RE = re.compile(r'^(Photos from \d{4}|Untitled\(\d+\))$', re.IGNORECASE)

# Leading year-first date in an album name, anchored at the start and followed
# by whitespace or the end of the name (separated or compact YYYYMMDD forms).
_LEADING_YEAR_FIRST_DATE_RE = re.compile(
    r'^(?P<date>(?:19|20)\d{2}[-/._]\d{1,2}[-/._]\d{1,2}|(?:19|20)\d{2}\d{2}\d{2})'
    r'(?=\s|$)(?P<rest>.*)$'
)


def _normalize_leading_date(name: str) -> Optional[str]:
    """Normalise a leading year-first date in an album name to YYYY-MM-DD.

    Returns the name with the date normalised, or None if the name does not
    start with a recognised, valid calendar date.
    """
    m = _LEADING_YEAR_FIRST_DATE_RE.match(name)
    if not m:
        return None
    date_part = m.group("date")
    if re.search(r'[-/._]', date_part):
        year, month, day = (int(part) for part in re.split(r'[-/._]', date_part))
    else:
        year, month, day = int(date_part[0:4]), int(date_part[4:6]), int(date_part[6:8])
    try:
        # Raises for months > 12 and impossible days (e.g. Feb 30)
        datetime(year, month, day)
    except ValueError:
        return None
    formatted = f"{year:04d}-{month:02d}-{day:02d}"
    rest = m.group("rest").strip()
    return f"{formatted} {rest}" if rest else formatted


def album_folder_name(name: str, oldest_dt: Optional[datetime]) -> str:
    """Return the album folder name, prefixed with its oldest file's date if needed.

    Shared by migration mode (``Albums/``) and import mode (``ImportedAlbums/``)
    so both apply the same date-prefix rules.
    """
    normalized = _normalize_leading_date(name)
    if normalized is not None:
        return normalized
    if oldest_dt is not None:
        return f"{oldest_dt:%Y-%m-%d} {name}"
    return name


# Backward-compatible alias for callers that used the original private name.
_format_album_name = album_folder_name


def create_album_symlinks(
    output_root: Path,
    album_files: dict,
    dry_run: bool,
    log: 'MigrationLog',
    root_name: str = "Albums",
    phase: str = "Phase 5",
):
    """Create <root_name>/<album_name>/ folders with symlinks to the actual files.

    `phase` is the printed progress header — migration mode runs this at
    Phase 5, import mode at Phase 4.
    """
    albums_dir = output_root / root_name
    real_albums = {name: paths for name, paths in album_files.items()
                   if not _GENERIC_ALBUM_RE.match(name) and len(paths) > 0}

    if not real_albums:
        print("No named albums to link.")
        return

    print(f"\n{phase}: Creating album symlinks for {len(real_albums)} albums...")
    link_count = 0
    skip_count = 0

    for album_name, dest_paths in sorted(real_albums.items()):
        # Items are either a Path (no date info — backward compatible) or a
        # (Path, dt) tuple. Derive the oldest dated file for the name prefix.
        oldest_dt = None
        resolved_paths = []
        for item in dest_paths:
            if isinstance(item, tuple):
                dest_path, dt = item
                if dt is not None and (oldest_dt is None or dt < oldest_dt):
                    oldest_dt = dt
            else:
                dest_path = item
            resolved_paths.append(dest_path)

        # Sanitize album name for filesystem
        candidate_name = album_folder_name(album_name, oldest_dt)
        candidate_name = candidate_name.replace("/", "-").replace(":", "-").strip()
        if not candidate_name:
            continue
        safe_name = candidate_name

        # Reuse an existing dated dir with the same base name: on rerun the
        # symlinks for a leaf go into the pinned dated dir instead of creating
        # a second dated dir (e.g. '2020-01-01 family' reused in a later run
        # that would have computed '2020-03-03 family'). Base comparison is on
        # the leaf's raw name (before any date prefix), so 'family' matches
        # '2020-01-01 family'.
        for existing in sorted(albums_dir.iterdir()) if albums_dir.exists() else []:
            if not existing.is_dir():
                continue
            m = re.match(r"^\d{4}-\d{2}-\d{2}\s+(?P<rest>.*)$", existing.name)
            if not m:
                continue
            base_name = m.group("rest").strip()
            if base_name == album_name.strip():
                safe_name = existing.name
                break

        album_dir = albums_dir / safe_name

        if not dry_run:
            album_dir.mkdir(parents=True, exist_ok=True)
            # Remove a legacy folder left by an earlier run under the album's
            # original (unprefixed) name, so reruns do not accumulate duplicate
            # folders. Only folders whose entries are all symlinks are removed
            # — anything else is left in place to avoid deleting user data.
            legacy_name = album_name.replace("/", "-").replace(":", "-").strip()
            if legacy_name and legacy_name != safe_name:
                legacy_dir = albums_dir / legacy_name
                if legacy_dir != album_dir and legacy_dir.is_dir() \
                        and not legacy_dir.is_symlink():
                    try:
                        entries = list(legacy_dir.iterdir())
                    except OSError as e:
                        log.log(f"ALBUM_STALE_ERROR: {legacy_dir.name} -- {e}")
                    else:
                        if all(entry.is_symlink() for entry in entries):
                            shutil.rmtree(legacy_dir)
                            log.log(f"ALBUM_RENAME: {legacy_dir.name} -> {safe_name}")
                        else:
                            log.log(f"ALBUM_STALE: {legacy_dir.name} left in place "
                                    f"(contains non-symlink entries)")

        for dest_path in resolved_paths:
            link_path = album_dir / dest_path.name
            if link_path.exists() or link_path.is_symlink():
                skip_count += 1
                continue
            if not dry_run:
                try:
                    # Use relative symlink so it works if the root is moved
                    rel_target = os.path.relpath(dest_path, album_dir)
                    link_path.symlink_to(rel_target)
                    link_count += 1
                except Exception as e:
                    log.log(f"SYMLINK_ERROR: {link_path} -> {dest_path} -- {e}")
            else:
                link_count += 1

    print(f"  Created {link_count} symlinks across {len(real_albums)} albums"
          f" ({skip_count} already existed)")
    log.log(f"ALBUMS: {link_count} symlinks in {len(real_albums)} albums")
