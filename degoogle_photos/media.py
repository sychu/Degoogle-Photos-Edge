"""RAW camera-file classification.

Owns the set of RAW extensions and the ``is_raw_file`` predicate so that
``copy.py``, ``report.py`` and ``cli.py`` can all classify RAW files without
importing each other. Pure stdlib (``pathlib``), no package imports.
"""

from pathlib import Path

# Popular RAW camera-file extensions, broad enough to cover the common formats
# while staying conservative (video RAW like .r3d / .braw is out of scope).
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
    """True when ``name`` (a filename or Path) has a RAW extension (case-insensitive)."""
    return Path(name).suffix.lower() in RAW_EXTENSIONS
