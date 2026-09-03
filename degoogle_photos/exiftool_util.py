"""Optional exiftool integration via pyexiftool's persistent ``-stay_open`` batch mode.

pyexiftool drives Phil Harvey's ``exiftool`` binary in ``-stay_open`` mode — one
persistent process serves a whole run, and each query is a cheap round-trip
rather than a fresh per-file spawn. The binary is an optional system dependency:
if it is missing (or the import fails), ``is_available()`` returns False and
every call degrades to ``None`` / ``{}`` — identical to today's behaviour.
"""

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

# Extensions Pillow reliably reads EXIF from. exiftool never runs for these, so
# the hot path stays free of subprocess round-trips.
PILLOW_EXIF_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png"}

# exiftool's default date format (no timezone offset).
_EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"

# Priority order of embedded date tags.
_DATE_KEYS = (
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "QuickTime:CreateDate",
    "QuickTime:CreationDate",
    "XMP:CreateDate",
)

# A trailing timezone offset (e.g. "+02:00") that some tags carry; stripped
# before parsing so we can still use the base ``%H:%M:%S`` format.
_OFFSET_RE = re.compile(r"([+-]\d{2}:?\d{2})\s*$")

_available: Optional[bool] = None
_client = None


def is_available() -> bool:
    """Return True when pyexiftool is importable and the ``exiftool`` binary is on PATH.

    The result is cached for the lifetime of the process. Any import error is
    treated as unavailable (the fallback is best-effort and never required).
    """
    global _available
    if _available is None:
        try:
            import exiftool  # noqa: F401
            _available = shutil.which("exiftool") is not None
        except Exception:
            _available = False
    return _available


def _get_client():
    """Return the lazily-created, shared ExifToolHelper instance."""
    global _client
    if _client is None:
        import exiftool
        # auto_start=True (default) begins the persistent process on first use.
        _client = exiftool.ExifToolHelper()
    return _client


def shutdown() -> None:
    """Terminate the persistent exiftool process (call at the end of a run)."""
    global _client
    if _client is not None:
        try:
            _client.terminate()
        except Exception:
            pass
        _client = None


def _strip_offset(value) -> str:
    value = _OFFSET_RE.sub("", str(value))
    return value.strip().strip("\x00")


def _first_date(meta: dict) -> Optional[datetime]:
    for key in _DATE_KEYS:
        raw = meta.get(key)
        if not raw:
            continue
        try:
            dt = datetime.strptime(_strip_offset(raw), _EXIF_DATE_FORMAT)
            if dt.year >= 1970:
                return dt
        except (ValueError, TypeError, OSError):
            continue
    return None


def date_from_exiftool(path: Path) -> Optional[datetime]:
    """Return an embedded date from ``path`` via exiftool, or None (best-effort)."""
    if not is_available():
        return None
    try:
        meta = _get_client().get_metadata([str(path)])
    except Exception:
        return None
    if not isinstance(meta, list) or not meta or not isinstance(meta[0], dict):
        return None
    return _first_date(meta[0])


_DMS_RE = re.compile(r"([0-9.]+)\s*deg\s*([0-9.]+)'\s*([0-9.]+)\"")


def _parse_gps(value) -> Optional[float]:
    """Parse an exiftool GPS coordinate into decimal degrees (tolerant)."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        pass
    try:
        m = _DMS_RE.search(str(value))
        if m:
            d, m, s = (float(x) for x in m.groups())
            return d + m / 60 + s / 3600
    except (ValueError, TypeError):
        pass
    return None


def _map_metadata(meta: dict) -> dict:
    """Map exiftool tags onto the tooltip keys used by report.py."""
    out: dict = {}

    # camera (Make + Model, from EXIF or QuickTime)
    make = meta.get("EXIF:Make") or meta.get("QuickTime:Make")
    model = meta.get("EXIF:Model") or meta.get("QuickTime:Model")
    camera = f"{make} {model}".strip() if make or model else None
    if camera:
        out["camera"] = camera

    # dimensions (ImageSize → W×H)
    size = meta.get("Composite:ImageSize") or meta.get("QuickTime:ImageSize")
    if size:
        try:
            w_h = str(size).split()
            if len(w_h) == 2 and w_h[0].isdigit() and w_h[1].isdigit():
                out["dimensions"] = f"{w_h[0]}\u00d7{w_h[1]}"
        except (ValueError, TypeError):
            pass

    iso = meta.get("EXIF:ISO") or meta.get("EXIF:ISOSpeedRatings")
    if iso is not None and str(iso) not in ("0", "0.0", ""):
        out["iso"] = f"ISO {iso}"

    focal = meta.get("EXIF:FocalLength") or meta.get("QuickTime:FocalLength")
    if focal is not None:
        try:
            out["focal_length"] = f"{float(focal):.0f}mm"
        except (ValueError, TypeError):
            out["focal_length"] = str(focal)

    fnumber = meta.get("EXIF:FNumber")
    if fnumber is not None:
        try:
            out["aperture"] = f"f/{float(fnumber):.1f}"
        except (ValueError, TypeError):
            pass

    # GPS: prefer the composite decimal values; fall back to DMS + reference.
    lat = _parse_gps(meta.get("Composite:GPSLatitude"))
    lon = _parse_gps(meta.get("Composite:GPSLongitude"))
    if lat is None and meta.get("GPS:GPSLatitude") is not None:
        lat = _parse_gps(meta.get("GPS:GPSLatitude"))
        if meta.get("GPS:GPSLatitudeRef") in ("S", "W") and lat is not None:
            lat = -lat
    if lon is None and meta.get("GPS:GPSLongitude") is not None:
        lon = _parse_gps(meta.get("GPS:GPSLongitude"))
        if meta.get("GPS:GPSLongitudeRef") in ("S", "W") and lon is not None:
            lon = -lon
    if lat is not None and lon is not None:
        out["gps"] = f"{lat:.4f}, {lon:.4f}"

    return out


def metadata_from_exiftool(path: Path) -> dict:
    """Return mapping of tooltip metadata keys from ``path``, or {} (best-effort)."""
    if not is_available():
        return {}
    try:
        meta = _get_client().get_metadata([str(path)])
    except Exception:
        return {}
    if not isinstance(meta, list) or not meta or not isinstance(meta[0], dict):
        return {}
    return _map_metadata(meta[0])
