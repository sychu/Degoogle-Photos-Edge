"""Deduplication via MD5 hashing and date-rounded keys."""

import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


def compute_md5(file_path: Path) -> str:
    """Compute MD5 hash of a file."""
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)  # 1 MB chunks
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_files(
    files: List[Path],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Dict[Path, str]:
    """
    Hash each file once and return a Path -> md5 map.

    progress_cb(current, total) is called after each file is hashed, if provided.
    """
    total = len(files)
    result: Dict[Path, str] = {}
    for i, fpath in enumerate(files, 1):
        result[fpath] = compute_md5(fpath)
        if progress_cb:
            progress_cb(i, total)
    return result


def group_duplicates(
    files: List[Path],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> List[Tuple[str, List[Path]]]:
    """
    Scan files by MD5 and return duplicate groups.

    Returns a list of (md5, [path, ...]) tuples where each list has 2+ files.
    Within each group the files are sorted shortest-path-first; the first entry
    is the suggested keeper and the rest are the candidates for deletion.

    progress_cb(current, total) is called after each file if provided.
    """
    file_md5 = hash_files(files, progress_cb=progress_cb)

    md5_groups: Dict[str, List[Path]] = defaultdict(list)
    for fpath, md5 in file_md5.items():
        md5_groups[md5].append(fpath)

    result = []
    for md5, group in md5_groups.items():
        if len(group) > 1:
            group.sort(key=lambda p: (len(str(p)), str(p)))
            result.append((md5, group))

    return result


def make_dedup_key(md5: str, dt: Optional[datetime]) -> tuple:
    """Create deduplication key: (md5, date rounded to minute)."""
    if dt:
        rounded = dt.replace(second=0, microsecond=0)
        return (md5, rounded.isoformat())
    return (md5, None)
