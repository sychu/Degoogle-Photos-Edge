"""HTML report generation for migration results."""

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .media import is_raw_file

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp", ".tiff", ".tif"}

HTML_UPDATE_INTERVAL = 200  # write HTML every N files

# Generic album names that Google auto-creates — not real user albums
_GENERIC_ALBUM_RE = re.compile(r'^(Photos from \d{4}|Untitled\(\d+\))$', re.IGNORECASE)


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _js_string(s: str) -> str:
    """Escape a string for embedding in a single-quoted JS literal within an HTML attribute."""
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")
    return _html_escape(s)


def _slugify(name: str) -> str:
    """Convert an album name to a filesystem/URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    return s.strip('_')[:80] or 'unnamed'


class HtmlReport:
    """Generates a multi-page browsable HTML report of the migration.

    Pages live under ``<output_root>/Reports/DeGoogle Reports/``. Each
    migration run can be isolated in its own timestamped ``migration-<ts>/``
    subdirectory via ``begin_run()``/``finish_run()``; the report root's
    ``index.html`` then lists all runs, newest first.
    """

    run_prefix: str = "migration"
    runs_title: str = "Migration Reports"

    def __init__(self, output_root: Path, dry_run: bool):
        self.output_root = output_root
        self.dry_run = dry_run
        self.report_dir = output_root / "Reports" / "DeGoogle Reports"
        self.report_root = self.report_dir
        self.run_dir: Optional[Path] = None
        self.report_title = "Degoogle-Photos Report"
        # files_by_folder["2020/03"] = [{"name": ..., "dest": ..., ...}, ...]
        self.files_by_folder = defaultdict(list)  # type: dict[str, list]
        # files_by_album["My Vacation"] = [{"name": ..., ...}, ...]
        self.files_by_album = defaultdict(list)   # type: dict[str, list]
        self.duplicates = []   # type: list[dict]
        self.errors = []       # type: list[dict]
        self.date_source_counts = defaultdict(int)  # type: dict[str, int]
        self.total = 0
        self.processed = 0
        self._dirty = False
        # Track which folders/albums changed since last write
        self._dirty_folders = set()
        self._dirty_albums = set()

    def add_copied(self, dest_path: Path, source_path: Path, dt: Optional[datetime],
                   date_source: str, album: str, had_json: bool,
                   metadata: Optional[dict] = None):
        is_raw = is_raw_file(dest_path.name)
        if not dt:
            folder = "Raw/Needs Review" if is_raw else "Needs Review"
        elif date_source == "parent_dir":
            folder = f"Raw/{dt.year:04d}/unknown" if is_raw else f"{dt.year:04d}/unknown"
        else:
            folder = (f"Raw/{dt.year:04d}/{dt.month:02d}"
                      if is_raw else f"{dt.year:04d}/{dt.month:02d}")
        entry = {
            "name": dest_path.name,
            "dest": str(dest_path),
            "source": str(source_path),
            "date": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "",
            "date_source": date_source,
            "album": album,
            "had_json": had_json,
            "is_image": dest_path.suffix.lower() in IMAGE_EXTENSIONS,
            "is_raw": is_raw,
            "metadata": metadata or {},
        }
        self.files_by_folder[folder].append(entry)
        self.date_source_counts[date_source] += 1
        self._dirty = True
        self._dirty_folders.add(folder)
        # Track album membership (skip generic "Photos from YYYY" albums)
        if album and not _GENERIC_ALBUM_RE.match(album):
            self.files_by_album[album].append(entry)
            self._dirty_albums.add(album)

    def add_duplicate(self, source_path: Path, md5: str):
        self.duplicates.append({"source": str(source_path), "md5": md5})
        self._dirty = True

    def add_error(self, source_path: Path, error: str):
        self.errors.append({"source": str(source_path), "error": error})
        self._dirty = True

    def maybe_write(self, current: int):
        """Write HTML if enough files have been processed since last write."""
        if current % HTML_UPDATE_INTERVAL == 0 or current == self.total:
            if self._dirty:
                self._write()
                self._dirty = False

    # ------------------------------------------------------------------
    # Multi-page write
    # ------------------------------------------------------------------

    @staticmethod
    def _folder_slug(folder: str) -> str:
        """Filesystem/URL-safe slug for a folder key (slashes and spaces → ``_``)."""
        return folder.replace("/", "_").replace(" ", "_")

    def _raw_folders(self) -> list:
        """Folder keys (Raw/YYYY/MM, Raw/YYYY/unknown, Raw/Needs Review) that hold RAW files."""
        return sorted(f for f in self.files_by_folder if f.startswith("Raw/"))

    def _raw_total(self) -> int:
        """Total number of RAW files recorded."""
        return sum(len(self.files_by_folder[f]) for f in self._raw_folders())

    def begin_run(self):
        """Open a new timestamped run directory and redirect report writes into it.

        Called before processing starts so incremental ``_write()`` calls
        (live progress updates) land inside ``<prefix>-<ts>/``;
        ``finish_run()`` restores the report root and refreshes the runs
        listing.
        """
        self.run_dir = self.report_root / f"{self.run_prefix}-{datetime.now():%Y%m%d-%H%M%S-%f}"
        self.report_dir = self.run_dir

    def finish_run(self):
        """Close the current run: restore the report root and rewrite the runs listing."""
        if self.run_dir is None:
            return
        self.report_dir = self.report_root
        self._write_runs_index(self.runs_title)

    def _write(self):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._write_css()
        self._write_index()
        # Only rewrite pages whose content changed
        for folder in self._dirty_folders:
            self._write_folder_page(folder, self.files_by_folder[folder])
        for album in self._dirty_albums:
            self._write_album_page(album, self.files_by_album[album])
        self._dirty_folders.clear()
        self._dirty_albums.clear()

    def _write_css(self):
        css_path = self.report_dir / "style.css"
        css_path.write_text(_CSS, encoding="utf-8")

    def _page_head(self, title: str, back_link: bool = False) -> str:
        parts = [
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<title>{_html_escape(title)}</title>',
            '<link rel="stylesheet" href="style.css">',
            '<script>function copyText(btn,t){navigator.clipboard.writeText(t).then(function(){'
            'var o=btn.textContent;btn.textContent="Copied!";setTimeout(function(){btn.textContent=o},1000)})}</script>',
            '</head><body>',
        ]
        if back_link:
            parts.append('<nav class="back"><a href="index.html">&larr; Back to Dashboard</a></nav>')
        return '\n'.join(parts)

    def _extra_stats(self, html: list):
        """Hook for subclasses: append extra summary stats to the stat-grid."""

    def _extra_sections(self, html: list):
        """Hook for subclasses: append extra sections before the footer."""

    def _write_index(self):
        total_copied = sum(len(v) for v in self.files_by_folder.values())
        total_dupes = len(self.duplicates)
        total_errors = len(self.errors)

        html = []
        prefix = "[DRY RUN] " if self.dry_run else ""
        html.append(self._page_head(f"{prefix}{self.report_title}"))

        html.append(f'<header><h1>{prefix}{self.report_title}</h1>')
        html.append(f'<p class="updated">Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                     f' &mdash; {self.processed}/{self.total} files processed</p></header>')

        # Stats
        html.append('<section class="summary"><h2>Summary</h2><div class="stat-grid">')
        html.append(f'<div class="stat"><span class="num">{total_copied}</span><span class="label">Copied</span></div>')
        html.append(f'<div class="stat"><span class="num">{total_dupes}</span><span class="label">Duplicates skipped</span></div>')
        html.append(f'<div class="stat"><span class="num">{total_errors}</span><span class="label">Errors</span></div>')
        nr = len(self.files_by_folder.get("Needs Review", []))
        if nr > 0:
            nr_slug = self._folder_slug("Needs Review")
            html.append(f'<div class="stat"><span class="num"><a href="folder_{nr_slug}.html">{nr}</a></span>'
                        f'<span class="label">Needs review</span></div>')
        unknown_folders = sorted(f for f in self.files_by_folder if f.endswith("/unknown"))
        unknown_total = sum(len(self.files_by_folder[f]) for f in unknown_folders)
        if unknown_total > 0:
            html.append(f'<div class="stat"><span class="num"><a href="#attention-needed">{unknown_total}</a></span>'
                        f'<span class="label">Unknown month</span></div>')
        raw_folders = self._raw_folders()
        raw_total = self._raw_total()
        if raw_total > 0:
            html.append(f'<div class="stat"><span class="num"><a href="#raw-files">{raw_total}</a></span>'
                        f'<span class="label">RAW files</span></div>')
        self._extra_stats(html)
        html.append('</div>')

        # Date source breakdown
        html.append('<h3>Date Sources</h3><table class="date-sources"><tr><th>Source</th><th>Count</th></tr>')
        source_labels = {
            "exif": "EXIF DateTimeOriginal",
            "exiftool": "EXIF/QuickTime (exiftool)",
            "json_taken": "JSON photoTakenTime",
            "filename": "Filename pattern",
            "json_created": "JSON creationTime",
            "parent_dir": "Parent directory year",
            "none": "No date found",
        }
        for key in ["exif", "exiftool", "json_taken", "filename", "json_created", "parent_dir", "none"]:
            cnt = self.date_source_counts.get(key, 0)
            if cnt > 0:
                html.append(f'<tr><td>{source_labels.get(key, key)}</td><td>{cnt}</td></tr>')
        html.append('</table></section>')

        # Attention needed section (only when such folders are non-empty)
        raw_nr = len(self.files_by_folder.get("Raw/Needs Review", []))
        if nr > 0 or raw_nr > 0 or unknown_folders:
            html.append('<section class="attention" id="attention-needed"><h2>Attention Needed</h2>')
            if nr > 0:
                nr_slug = self._folder_slug("Needs Review")
                html.append(f'<p><a href="folder_{nr_slug}.html">Needs Review</a> &mdash; '
                            f'No date found from any source ({nr} files)</p>')
            if raw_nr > 0:
                nr_slug = self._folder_slug("Raw/Needs Review")
                html.append(f'<p><a href="folder_{nr_slug}.html">Raw / Needs Review</a> &mdash; '
                            f'No date found from any source ({raw_nr} files)</p>')
            for folder in unknown_folders:
                count = len(self.files_by_folder[folder])
                slug = self._folder_slug(folder)
                html.append(f'<p><a href="folder_{slug}.html">{folder}</a> &mdash; '
                            f'Year known from parent folder, month unknown ({count} files)</p>')
            html.append('</section>')

        # RAW Files section (only when at least one RAW file was spotted)
        if raw_total > 0:
            html.append('<section id="raw-files"><h2>RAW Files</h2>')
            for folder in raw_folders:
                count = len(self.files_by_folder[folder])
                slug = self._folder_slug(folder)
                css = ' class="review"' if folder == "Raw/Needs Review" or folder.endswith("/unknown") else ""
                html.append(f'<p><a href="folder_{slug}.html"{css}>{folder}</a> &mdash; '
                            f'{count} files</p>')
            html.append('</section>')

        # Album navigation
        if self.files_by_album:
            html.append('<section class="nav-section"><h2>Albums</h2><div class="folder-nav">')
            for album in sorted(self.files_by_album.keys()):
                count = len(self.files_by_album[album])
                slug = _slugify(album)
                html.append(f'<a href="album_{slug}.html">{_html_escape(album)} ({count})</a>')
            html.append('</div></section>')

        # Folder navigation
        html.append('<section class="nav-section"><h2>Browse by Date Folder</h2><div class="folder-nav">')
        for folder in sorted(self.files_by_folder.keys()):
            count = len(self.files_by_folder[folder])
            slug = self._folder_slug(folder)
            css = (' class="review"' if folder in ("Needs Review", "Raw/Needs Review")
                   or folder.endswith("/unknown") else "")
            html.append(f'<a href="folder_{slug}.html"{css}>{folder} ({count})</a>')
        html.append('</div></section>')

        # Duplicates
        if self.duplicates:
            html.append('<section class="dupes"><h2>Duplicates Skipped</h2>')
            html.append(f'<p>{len(self.duplicates)} duplicate files were skipped.</p>')
            html.append('<details><summary>Show all duplicates</summary><table><tr><th>Source</th><th>MD5</th></tr>')
            for d in self.duplicates:
                html.append(f'<tr><td>{_html_escape(d["source"])}</td><td><code>{d["md5"]}</code></td></tr>')
            html.append('</table></details></section>')

        # Errors
        if self.errors:
            html.append('<section class="errors"><h2>Errors</h2>')
            html.append('<table><tr><th>Source</th><th>Error</th></tr>')
            for e in self.errors:
                html.append(f'<tr><td>{_html_escape(e["source"])}</td><td>{_html_escape(e["error"])}</td></tr>')
            html.append('</table></section>')

        self._extra_sections(html)

        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / "index.html").write_text("\n".join(html), encoding="utf-8")

    def _write_folder_page(self, folder: str, files: list):
        slug = self._folder_slug(folder)
        html = []
        html.append(self._page_head(f"Folder: {folder}", back_link=True))
        html.append(f'<h1>{folder} <span class="count">({len(files)} files)</span></h1>')
        if folder in ("Needs Review", "Raw/Needs Review"):
            html.append('<p class="updated">No date found from any source. '
                        'Review and move to the correct YYYY/MM/ folder.</p>')
        elif folder.endswith("/unknown"):
            html.append('<p class="updated">Year known from parent folder, month unknown.</p>')
        html.append('<div class="file-grid">')
        for f in files:
            html.append(self._render_card(f))
        html.append('</div>')
        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / f"folder_{slug}.html").write_text("\n".join(html), encoding="utf-8")

    def _write_album_page(self, album: str, files: list):
        slug = _slugify(album)
        html = []
        html.append(self._page_head(f"Album: {album}", back_link=True))
        html.append(f'<h1>Album: {_html_escape(album)} <span class="count">({len(files)} files)</span></h1>')
        html.append('<div class="file-grid">')
        for f in files:
            html.append(self._render_card(f))
        html.append('</div>')
        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / f"album_{slug}.html").write_text("\n".join(html), encoding="utf-8")

    def _write_runs_index(self, title: str):
        """Regenerate the report root's index.html listing all run dirs, newest first."""
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._write_css()
        prefix_dash = f"{self.run_prefix}-"
        run_dirs = sorted(
            (d for d in self.report_dir.iterdir()
             if d.is_dir() and d.name.startswith(prefix_dash)
             and (d / "index.html").exists()),
            key=lambda d: d.name, reverse=True,
        )
        html = [self._page_head(title)]
        html.append(f'<header><h1>{title}</h1>')
        html.append(f'<p class="updated">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                    f' &mdash; {len(run_dirs)} run(s)</p></header>')
        html.append('<section class="summary"><h2>Summary</h2><div class="stat-grid">')
        html.append(f'<div class="stat"><span class="num">{len(run_dirs)}</span>'
                    f'<span class="label">{self.run_prefix.capitalize()} runs</span></div>')
        if run_dirs:
            html.append(f'<div class="stat"><span class="num"><a href="{run_dirs[0].name}/index.html">'
                        f'{len(run_dirs)}</a></span><span class="label">Newest run</span></div>')
        html.append('</div></section>')
        if not run_dirs:
            html.append(f'<p>No {self.run_prefix} runs yet.</p>')
        else:
            html.append('<section><h2>Runs</h2><table><tr><th>Run</th><th>Report</th></tr>')
            for d in run_dirs:
                stamp = d.name[len(prefix_dash):]
                try:
                    pretty = datetime.strptime(stamp, "%Y%m%d-%H%M%S-%f").strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pretty = stamp
                html.append(f'<tr><td>{_html_escape(pretty)}</td>'
                            f'<td><a class="finder-btn" href="{d.name}/index.html">Open report</a></td></tr>')
            html.append('</table></section>')
        html.append(_FOOTER)
        html.append('</body></html>')
        (self.report_dir / "index.html").write_text("\n".join(html), encoding="utf-8")

    # ------------------------------------------------------------------
    # Card rendering
    # ------------------------------------------------------------------

    def _render_card(self, f: dict) -> str:
        meta = f.get("metadata", {})

        # Thumbnail
        if f["is_image"]:
            thumb = (f'<div class="thumb"><img loading="lazy" '
                     f'src="file://{_html_escape(f["dest"])}" '
                     f'alt="{_html_escape(f["name"])}"></div>')
        else:
            ext = Path(f["name"]).suffix.upper()
            thumb = f'<div class="thumb vid-thumb">{ext}</div>'

        # EXIF badge with tooltip
        exif_parts = [v for k, v in meta.items()
                      if k in ("camera", "dimensions", "iso", "focal_length", "aperture", "gps")]
        if exif_parts:
            exif_tip = _html_escape(" | ".join(exif_parts))
            src_badge = (f'<span class="badge badge-{f["date_source"]} has-tooltip" '
                         f'data-tooltip="{exif_tip}">{f["date_source"]}</span>')
        else:
            src_badge = f'<span class="badge badge-{f["date_source"]}">{f["date_source"]}</span>'

        # JSON badge with tooltip
        if f["had_json"]:
            json_parts = []
            for key, label in [("photoTakenTime", "Taken"), ("people", "People"),
                                ("geo", "Geo"), ("description", "Desc"),
                                ("device_type", "Device"), ("google_url", "URL")]:
                val = meta.get(key)
                if val:
                    json_parts.append(f"{label}: {val}")
            if json_parts:
                json_tip = _html_escape(" | ".join(json_parts))
                json_badge = (f'<span class="badge badge-json has-tooltip" '
                              f'data-tooltip="{json_tip}">JSON</span>')
            else:
                json_badge = '<span class="badge badge-json">JSON</span>'
        else:
            json_badge = ""

        # View in Finder button
        parent_dir = str(Path(f["dest"]).parent)
        finder_btn = (f'<a class="finder-btn" href="file://{_html_escape(parent_dir)}/" '
                      f'title="Open folder in Finder">Finder</a>')

        raw_badge = '<span class="badge badge-raw">RAW</span>' if f.get("is_raw") else ""

        # Copy buttons (clipboard icon: &#x1f4cb;)
        copy_name_btn = (f'<button class="copy-btn" onclick="copyText(this, \'{_js_string(f["name"])}\')" '
                         f'title="Copy filename">&#x1f4cb; Name</button>')
        copy_path_btn = (f'<button class="copy-btn" onclick="copyText(this, \'{_js_string(f["dest"])}\')" '
                         f'title="Copy full path">&#x1f4cb; Path</button>')

        return (
            f'<div class="file-card">'
            f'{thumb}'
            f'<div class="file-info">'
            f'<div class="file-name" title="{_html_escape(f["name"])}">{_html_escape(f["name"])}</div>'
            f'<div class="file-date">{f["date"]}</div>'
            f'<div class="file-meta">{raw_badge} {src_badge} {json_badge} {finder_btn} {copy_name_btn} {copy_path_btn}</div>'
            f'<div class="file-album" title="{_html_escape(f["album"])}">Album: {_html_escape(f["album"])}</div>'
            f'</div></div>'
        )


class DedupReport:
    """HTML report for a dedup scan (no Takeout structure required).

    Writes to ``<output>/Reports/Dedup Reports/`` so it never clobbers the
    migration report at ``<output>/Reports/DeGoogle Reports/``.
    """

    def __init__(self, output_dir: Path, dry_run: bool, mode_label: str = "Dedup"):
        self.output_dir = output_dir
        self.dry_run = dry_run
        self.mode_label = mode_label
        self.report_dir = output_dir / "Reports" / "Dedup Reports"
        self.groups: list = []   # [{"md5": str, "files": [{"path", "name", "size", "keeper"}]}]
        self.scanned = 0
        self.total = 0
        self.copied = 0
        self.errors: list = []   # [{"path": str, "error": str}]
        self.attention: list = []  # [{"source", "dest", "date_source"}]
        self.raw_files: list = []  # [{"source", "dest"}] — RAW files copied to Raw/
        self.skipped_dest: list = []  # [{"source", "dest"}] — already in destination
        self.skipped_intra: list = []  # [{"source", "dest"}] — duplicates of files copied in this run

    def add_attention(self, src, dest, date_source):
        """Add a file that needs manual review (Needs Review/ or YYYY/unknown/)."""
        self.attention.append({"source": str(src), "dest": str(dest), "date_source": date_source})

    def add_raw(self, src, dest):
        """Add a RAW file copied into the detached Raw/ tree."""
        self.raw_files.append({"source": str(src), "dest": str(dest)})

    def add_skipped_dest(self, source: Path, dest: Path):
        """Record a source file skipped because its content already exists in the destination."""
        self.skipped_dest.append({"source": str(source), "dest": str(dest)})

    def add_skipped_intra(self, source: Path, dest: Path):
        """Record a source file skipped because its content was copied earlier in this run."""
        self.skipped_intra.append({"source": str(source), "dest": str(dest)})

    def add_group(self, md5: str, files):
        """Add a duplicate group. files is a list of Path; first entry is the keeper."""
        group_files = []
        for i, fpath in enumerate(files):
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0
            group_files.append({
                "path": str(fpath),
                "name": fpath.name,
                "size": size,
                "is_image": fpath.suffix.lower() in IMAGE_EXTENSIONS,
                "keeper": i == 0,
            })
        self.groups.append({"md5": md5, "files": group_files})

    def add_error(self, path, error: str):
        self.errors.append({"path": str(path), "error": error})

    def write(self):
        """Write the report to a timestamped run file and refresh index.html.

        Each run gets a ``dedup-YYYYMMDD-HHMMSS.html`` file so history from
        multiple imports is preserved; index.html always mirrors the latest
        run (and older timestamped files hang alongside it).
        """
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._write_css()
        html = self._build_index_html()
        # Microseconds keep back-to-back runs from colliding on the filename,
        # and zero-padded naming keeps the files lexicographically sortable.
        timestamped = f"dedup-{datetime.now():%Y%m%d-%H%M%S-%f}.html"
        (self.report_dir / timestamped).write_text(html, encoding="utf-8")
        (self.report_dir / "index.html").write_text(html, encoding="utf-8")

    # ------------------------------------------------------------------

    def _write_css(self):
        (self.report_dir / "style.css").write_text(_CSS, encoding="utf-8")

    def _build_index_html(self) -> str:
        """Build and return the report's index page content (no file write)."""
        dupe_file_count = sum(len(g["files"]) - 1 for g in self.groups)
        wasted_bytes = sum(
            f["size"] for g in self.groups for f in g["files"] if not f["keeper"]
        )

        prefix = "[DRY RUN] " if self.dry_run else ""
        html = []
        html.append(
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{prefix}{self.mode_label} Report</title>'
            '<link rel="stylesheet" href="style.css">'
            '<script>function copyText(btn,t){navigator.clipboard.writeText(t).then(function(){'
            'var o=btn.textContent;btn.textContent="Copied!";setTimeout(function(){btn.textContent=o},1000)})}</script>'
            '</head><body>'
        )
        subtitle = ("Source is read-only. New content was merged into the existing library."
                    if self.mode_label == "Dedup-import" else
                    "Source is read-only. One file per duplicate group was copied to the output folder.")
        html.append(f'<header><h1>{prefix}{self.mode_label} Report</h1>'
                    f'<p class="updated" style="color:#8b949e;font-size:0.9em;margin-top:4px">'
                    f'{subtitle}</p>')
        html.append(f'<p class="updated">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                    f' &mdash; {self.scanned}/{self.total} files scanned</p></header>')

        # Summary stats
        html.append('<section class="summary"><h2>Summary</h2><div class="stat-grid">')
        html.append(f'<div class="stat"><span class="num">{self.scanned}</span><span class="label">Files scanned</span></div>')
        html.append(f'<div class="stat"><span class="num">{self.copied}</span><span class="label">Unique files copied</span></div>')
        html.append(f'<div class="stat"><span class="num">{len(self.groups)}</span><span class="label">Duplicate groups</span></div>')
        html.append(f'<div class="stat"><span class="num">{dupe_file_count}</span><span class="label">Duplicates skipped</span></div>')
        html.append(f'<div class="stat"><span class="num">{_fmt_bytes(wasted_bytes)}</span><span class="label">Space saved</span></div>')
        if self.skipped_dest:
            html.append(f'<div class="stat"><span class="num">{len(self.skipped_dest)}</span><span class="label">Already in destination</span></div>')
        if self.skipped_intra:
            html.append(f'<div class="stat"><span class="num">{len(self.skipped_intra)}</span><span class="label">Intra-run duplicates</span></div>')
        if self.raw_files:
            html.append(f'<div class="stat"><span class="num">{len(self.raw_files)}</span><span class="label">RAW files</span></div>')
        html.append('</div>')

        if not self.groups and not self.skipped_dest and not self.skipped_intra:
            html.append('<p style="color:#3fb950;margin-top:16px">No duplicates found.</p>')
        html.append('</section>')

        # Files needing attention (only when non-empty)
        if self.attention:
            unmatched = [a for a in self.attention if a["date_source"] == "none"]
            unknown = [a for a in self.attention if a["date_source"] == "parent_dir"]
            html.append('<section class="attention"><h2>Files Needing Attention</h2>')

            def _row(a):
                src_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(a["source"])}\')" '
                           f'title="Copy source path">&#x1f4cb; Src</button>')
                dst_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(a["dest"])}\')" '
                           f'title="Copy destination path">&#x1f4cb; Dest</button>')
                return (f'<tr><td style="font-size:0.8em;word-break:break-all">{_html_escape(a["source"])}</td>'
                        f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(a["dest"])}</td>'
                        f'<td style="white-space:nowrap">{src_btn} {dst_btn}</td></tr>')

            if unmatched:
                html.append('<h3>Unmatched (no date)</h3>')
                html.append('<table><tr><th>Source</th><th>Destination</th><th></th></tr>')
                for a in unmatched:
                    html.append(_row(a))
                html.append('</table>')
            if unknown:
                html.append('<h3>Unknown month</h3>')
                html.append('<table><tr><th>Source</th><th>Destination</th><th></th></tr>')
                for a in unknown:
                    html.append(_row(a))
                html.append('</table>')
            html.append('</section>')

        # Files already in the destination (import mode) / intra-run duplicates
        def _skip_table(rows, blurb):
            html.append(f'<p>{len(rows)} source files were skipped because {blurb}</p>')
            html.append('<table><tr><th>Source</th><th>Destination</th><th></th></tr>')
            for s in rows:
                src_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(s["source"])}\')" '
                           f'title="Copy source path">&#x1f4cb; Src</button>')
                dst_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(s["dest"])}\')" '
                           f'title="Copy destination path">&#x1f4cb; Dest</button>')
                html.append(f'<tr><td style="font-size:0.8em;word-break:break-all">{_html_escape(s["source"])}</td>'
                            f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(s["dest"])}</td>'
                            f'<td style="white-space:nowrap">{src_btn} {dst_btn}</td></tr>')
            html.append('</table>')

        if self.skipped_dest:
            html.append('<section><h2>Already in Destination</h2>')
            _skip_table(self.skipped_dest, 'their content already exists in the output.')
            html.append('</section>')

        if self.skipped_intra:
            html.append('<section><h2>Intra-run Duplicates</h2>')
            html.append(f'<p>{len(self.skipped_intra)} source files were skipped because '
                        'the source contained duplicate content; only the first copy was imported.</p>')
            html.append('<table><tr><th>Source</th><th>Destination</th><th></th></tr>')
            for s in self.skipped_intra:
                src_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(s["source"])}\')" '
                           f'title="Copy source path">&#x1f4cb; Src</button>')
                dst_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(s["dest"])}\')" '
                           f'title="Copy destination path">&#x1f4cb; Dest</button>')
                html.append(f'<tr><td style="font-size:0.8em;word-break:break-all">{_html_escape(s["source"])}</td>'
                            f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(s["dest"])}</td>'
                            f'<td style="white-space:nowrap">{src_btn} {dst_btn}</td></tr>')
            html.append('</table></section>')

        # RAW files (only when at least one RAW file was copied)
        if self.raw_files:
            html.append('<section><h2>RAW Files</h2>')
            html.append(f'<p>{len(self.raw_files)} RAW file(s) were copied into the '
                        '<code>Raw/</code> tree.</p>')
            html.append('<table><tr><th>Source</th><th>Destination</th><th></th></tr>')
            for r in self.raw_files:
                src_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(r["source"])}\')" '
                           f'title="Copy source path">&#x1f4cb; Src</button>')
                dst_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(r["dest"])}\')" '
                           f'title="Copy destination path">&#x1f4cb; Dest</button>')
                html.append(f'<tr><td style="font-size:0.8em;word-break:break-all">{_html_escape(r["source"])}</td>'
                            f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(r["dest"])}</td>'
                            f'<td style="white-space:nowrap">{src_btn} {dst_btn}</td></tr>')
            html.append('</table></section>')

        # Duplicate groups
        if self.groups:
            html.append('<section><h2>Duplicate Groups</h2>')
            for i, g in enumerate(self.groups, 1):
                group_wasted = sum(f["size"] for f in g["files"] if not f["keeper"])
                html.append(
                    f'<details open><summary>'
                    f'Group {i} &mdash; {len(g["files"])} copies &mdash; '
                    f'{_fmt_bytes(group_wasted)} wasted &mdash; '
                    f'<code>{g["md5"]}</code>'
                    f'</summary>'
                )
                html.append('<table><tr><th>Status</th><th>Path</th><th>Size</th><th></th></tr>')
                for f in g["files"]:
                    status_class = "keeper" if f["keeper"] else "dupe"
                    status_label = "COPIED" if f["keeper"] else "SKIPPED"
                    status_style = 'color:#3fb950' if f["keeper"] else 'color:#8b949e'
                    copy_btn = (
                        f'<button class="copy-btn" onclick="copyText(this, \'{_js_string(f["path"])}\')"'
                        f' title="Copy path">&#x1f4cb; Path</button>'
                    )
                    html.append(
                        f'<tr class="{status_class}">'
                        f'<td style="{status_style};font-weight:600">{status_label}</td>'
                        f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(f["path"])}</td>'
                        f'<td style="white-space:nowrap">{_fmt_bytes(f["size"])}</td>'
                        f'<td>{copy_btn}</td>'
                        f'</tr>'
                    )
                html.append('</table></details>')
            html.append('</section>')

        # Errors
        if self.errors:
            html.append('<section class="errors"><h2>Errors</h2>')
            html.append('<table><tr><th>Path</th><th>Error</th></tr>')
            for e in self.errors:
                html.append(f'<tr><td>{_html_escape(e["path"])}</td><td>{_html_escape(e["error"])}</td></tr>')
            html.append('</table></section>')

        html.append(_FOOTER)
        html.append('</body></html>')
        return "\n".join(html)


class ImportReport(HtmlReport):
    """Browsable report for ``--dedup-import`` runs.

    Reuses ``HtmlReport``'s date-folder/album structure and file cards (the
    source's immediate parent dir acts as the album name), and adds
    import-specific skip tables. Each run's pages live in a timestamped
    subdirectory (``Reports/Import Reports/import-<ts>/``) so history survives
    across imports; ``Reports/Import Reports/index.html`` is regenerated as a
    listing of all runs, newest first. Migration's ``Reports/DeGoogle
    Reports/`` is never touched.
    """

    run_prefix = "import"
    runs_title = "Dedup-import Reports"

    def __init__(self, output_root: Path, dry_run: bool):
        super().__init__(output_root, dry_run)
        self.report_dir = output_root / "Reports" / "Import Reports"
        self.report_root = self.report_dir
        self.report_title = "Dedup-import Report"
        self.scanned = 0
        self.copied = 0
        self.skipped_dest: list = []   # [{"source", "dest"}] — content existed pre-run
        self.skipped_intra: list = []  # [{"source", "dest"}] — source-internal duplicates

    def add_skipped_dest(self, source: Path, dest: Path):
        """Record a source file skipped because its content already exists in the destination."""
        self.skipped_dest.append({"source": str(source), "dest": str(dest)})

    def add_skipped_intra(self, source: Path, dest: Path):
        """Record a source file skipped because its content was copied earlier in this run."""
        self.skipped_intra.append({"source": str(source), "dest": str(dest)})

    def write(self):
        """Write this run's pages under Import Reports/import-<ts>/ and refresh
        the run listing at Import Reports/index.html."""
        self.processed = self.total  # written at end of run; header shows done state
        self.begin_run()
        try:
            self._write()
        finally:
            self.finish_run()

    def _extra_stats(self, html: list):
        if self.skipped_dest:
            html.append(f'<div class="stat"><span class="num">{len(self.skipped_dest)}</span>'
                        f'<span class="label">Already in destination</span></div>')
        if self.skipped_intra:
            html.append(f'<div class="stat"><span class="num">{len(self.skipped_intra)}</span>'
                        f'<span class="label">Intra-run duplicates</span></div>')

    def _extra_sections(self, html: list):
        imported = [f for files in self.files_by_folder.values() for f in files]
        if imported:
            html.append('<section><h2>New Files Imported</h2>')
            html.append(f'<p>{len(imported)} source files were copied into the library.</p>')
            html.append('<table><tr><th>Source</th><th>Destination</th><th>Album</th><th></th></tr>')
            for f in imported:
                src_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(f["source"])}\')" '
                           f'title="Copy source path">&#x1f4cb; Src</button>')
                dst_btn = (f'<button class="copy-btn" '
                           f'onclick="copyText(this, \'{_js_string(f["dest"])}\')" '
                           f'title="Copy destination path">&#x1f4cb; Dest</button>')
                html.append(f'<tr><td style="font-size:0.8em;word-break:break-all">{_html_escape(f["source"])}</td>'
                            f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(f["dest"])}</td>'
                            f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(f["album"])}</td>'
                            f'<td style="white-space:nowrap">{src_btn} {dst_btn}</td></tr>')
            html.append('</table></section>')

        if self.skipped_dest:
            html.append('<section><h2>Already in Destination</h2>')
            self._skip_table(html, self.skipped_dest,
                             'their content already exists in the output.')
            html.append('</section>')
        if self.skipped_intra:
            html.append('<section><h2>Intra-run Duplicates</h2>')
            self._skip_table(html, self.skipped_intra,
                             'the source contained duplicate content; '
                             'only the first copy was imported.')
            html.append('</section>')

    @staticmethod
    def _skip_table(html: list, rows: list, blurb: str):
        html.append(f'<p>{len(rows)} source files were skipped because {blurb}</p>')
        html.append('<table><tr><th>Source</th><th>Destination</th><th></th></tr>')
        for s in rows:
            src_btn = (f'<button class="copy-btn" '
                       f'onclick="copyText(this, \'{_js_string(s["source"])}\')" '
                       f'title="Copy source path">&#x1f4cb; Src</button>')
            dst_btn = (f'<button class="copy-btn" '
                       f'onclick="copyText(this, \'{_js_string(s["dest"])}\')" '
                       f'title="Copy destination path">&#x1f4cb; Dest</button>')
            html.append(f'<tr><td style="font-size:0.8em;word-break:break-all">{_html_escape(s["source"])}</td>'
                        f'<td style="font-size:0.8em;word-break:break-all">{_html_escape(s["dest"])}</td>'
                        f'<td style="white-space:nowrap">{src_btn} {dst_btn}</td></tr>')
        html.append('</table>')


def _fmt_bytes(n: int) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


_FOOTER = (
    '<footer class="site-footer">'
    'Generated by <a href="https://github.com/sychu/Degoogle-Photos-Edge">Degoogle-Photos-Edge</a>'
    '</footer>'
)

# ---------------------------------------------------------------------------
# Shared CSS
# ---------------------------------------------------------------------------

_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0d1117; color: #c9d1d9; padding: 20px; line-height: 1.5;
       max-width: 100vw; overflow-x: hidden; }
header { margin-bottom: 30px; }
h1 { color: #58a6ff; font-size: 1.6em; margin-bottom: 10px; }
h2 { color: #58a6ff; margin: 20px 0 12px; font-size: 1.3em; border-bottom: 1px solid #21262d; padding-bottom: 6px; }
h3 { color: #c9d1d9; margin: 14px 0 8px; font-size: 1.1em; }
.updated { color: #8b949e; font-size: 0.9em; margin-top: 4px; }
.back { margin-bottom: 16px; }
.back a { color: #58a6ff; text-decoration: none; font-size: 0.9em; }
.back a:hover { text-decoration: underline; }
.stat-grid { display: flex; gap: 16px; flex-wrap: wrap; margin: 10px 0; }
.stat { background: #161b22; border: 1px solid #21262d; border-radius: 8px;
        padding: 16px 24px; text-align: center; min-width: 140px; }
.stat .num { display: block; font-size: 2em; font-weight: 700; color: #58a6ff; }
.stat .label { color: #8b949e; font-size: 0.85em; }
.stat .num a { color: #58a6ff; text-decoration: none; }
.stat .num a:hover { text-decoration: underline; }
.attention { background: #f0883e0f; border: 1px solid #f0883e55; border-radius: 8px;
             padding: 12px 16px; margin: 20px 0; }
.attention h2 { color: #f0883e; border-bottom-color: #f0883e55; }
.attention a { color: #f0883e; font-weight: 600; text-decoration: none; }
.attention a:hover { text-decoration: underline; }
.attention p { margin: 6px 0; font-size: 0.9em; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #21262d; font-size: 0.85em; }
th { color: #8b949e; }
.date-sources { width: auto; }
.nav-section { margin-bottom: 24px; }
.folder-nav { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 20px; }
.folder-nav a { background: #161b22; border: 1px solid #21262d; border-radius: 6px;
                padding: 4px 10px; color: #58a6ff; text-decoration: none; font-size: 0.85em; }
.folder-nav a:hover { background: #1f2937; }
.folder-nav a.review { color: #f0883e; border-color: #f0883e; }
.count { color: #8b949e; font-weight: 400; font-size: 0.9em; }
.file-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.file-card { background: #161b22; border: 1px solid #21262d; border-radius: 8px; position: relative; }
.thumb { width: 100%; height: 160px; overflow: hidden; display: flex; align-items: center;
         justify-content: center; background: #0d1117; border-radius: 8px 8px 0 0; }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.vid-thumb { color: #8b949e; font-size: 1.4em; font-weight: 700; }
.file-info { padding: 8px 10px; overflow: visible; }
.file-name { font-size: 0.8em; font-weight: 600; color: #c9d1d9; white-space: nowrap;
             overflow: hidden; text-overflow: ellipsis; }
.file-date { font-size: 0.75em; color: #8b949e; margin: 2px 0; }
.file-meta { display: flex; gap: 4px; margin: 4px 0; flex-wrap: wrap; align-items: center; overflow: visible; }
.file-album { font-size: 0.7em; color: #6e7681; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.badge { font-size: 0.65em; padding: 1px 6px; border-radius: 10px; font-weight: 600; }
.badge-exif { background: #1f6feb33; color: #58a6ff; }
.badge-exiftool { background: #1f6feb33; color: #58a6ff; }
.badge-raw { background: #8b5cf633; color: #b8a1ff; }
.badge-json_taken { background: #23863633; color: #3fb950; }
.badge-filename { background: #9e6a03aa; color: #e3b341; }
.badge-json_created { background: #23863633; color: #3fb950; }
.badge-parent_dir { background: #f0883e33; color: #f0883e; }
.badge-none { background: #f8514933; color: #f85149; }
.badge-json { background: #23863633; color: #3fb950; }
/* Tooltip via data-tooltip + ::after */
.has-tooltip { position: relative; cursor: help; }
.has-tooltip:hover::after {
    content: attr(data-tooltip);
    position: absolute; bottom: 120%; left: 50%; transform: translateX(-50%);
    background: #1c2128; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px;
    padding: 6px 10px; font-size: 0.75em; font-weight: 400; white-space: pre-wrap;
    max-width: 320px; z-index: 100; pointer-events: none; line-height: 1.4;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
/* Finder button */
.finder-btn { font-size: 0.6em; padding: 1px 6px; border-radius: 10px; font-weight: 600;
              background: #30363d; color: #c9d1d9; text-decoration: none; border: 1px solid #484f58; }
.finder-btn:hover { background: #484f58; }
.copy-btn { font-size: 0.6em; padding: 1px 6px; border-radius: 10px; font-weight: 600;
            background: #30363d; color: #c9d1d9; border: 1px solid #484f58; cursor: pointer;
            font-family: inherit; }
.copy-btn:hover { background: #484f58; }
details { margin: 8px 0; }
summary { cursor: pointer; color: #58a6ff; font-size: 0.9em; }
.errors table td { color: #f85149; }
code { font-size: 0.8em; color: #8b949e; }
.site-footer { margin-top: 40px; padding: 16px 0; border-top: 1px solid #21262d;
               text-align: center; font-size: 0.8em; color: #8b949e; }
.site-footer a { color: #58a6ff; text-decoration: none; }
.site-footer a:hover { text-decoration: underline; }
"""
