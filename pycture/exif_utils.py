"""Extraction de la date de prise de vue depuis les métadonnées EXIF."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from PIL import Image
from PIL.ExifTags import TAGS

try:
    from PIL.ExifTags import IFD
except ImportError:  # pragma: no cover
    IFD = None  # type: ignore[misc, assignment]

DateSource = Literal[
    "exif_original",
    "exif_digitized",
    "exif_datetime",
    "filename",
    "mtime",
]


@dataclass(frozen=True)
class CaptureDate:
    value: datetime
    source: DateSource

    @property
    def from_exif(self) -> bool:
        return self.source.startswith("exif")

    @property
    def is_reliable(self) -> bool:
        """EXIF prise de vue / numérisation, ou date explicite dans le nom."""
        return self.source in (
            "exif_original",
            "exif_digitized",
            "filename",
        )

# Extensions d'images supportées
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".webp",
    ".bmp",
    ".gif",
    ".raw",
    ".cr2",
    ".nef",
    ".arw",
    ".dng",
    ".orf",
    ".rw2",
}

# Vidéos (AVI et formats courants) → dossier année/video
VIDEO_EXTENSIONS = {
    ".avi",
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".mts",
    ".m2ts",
}

# Fichiers macOS / système à ignorer (AppleDouble ._* , etc.)
JUNK_FILENAMES = {
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
    ".localized",
}

# IDs EXIF
_TAG_DATETIME = 306  # DateTime — souvent date de dernier enregistrement logiciel
_TAG_DATETIME_ORIGINAL = 36867  # DateTimeOriginal — vraie prise de vue
_TAG_DATETIME_DIGITIZED = 36868  # DateTimeDigitized — numérisation


def is_junk_file(path: Path) -> bool:
    """True pour les métadonnées macOS (._*) et autres fichiers système parasites."""
    name = path.name
    if name.startswith("._"):
        return True
    if name.startswith(".__"):
        return True
    if name.lower() in JUNK_FILENAMES:
        return True
    return False


def is_image(path: Path) -> bool:
    if not path.is_file() or is_junk_file(path):
        return False
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_video(path: Path) -> bool:
    if not path.is_file() or is_junk_file(path):
        return False
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_media(path: Path) -> bool:
    return is_image(path) or is_video(path)


def _file_datetime(path: Path) -> datetime:
    """Date de modification du fichier (dernier recours)."""
    return datetime.fromtimestamp(path.stat().st_mtime)


def _parse_exif_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.startswith("0000"):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_datetime_from_filename(name: str) -> datetime | None:
    """
    Extrait une date explicite du nom de fichier.

    Formats reconnus (du plus précis au plus court) :
    - aaaa-mm-jj hh-mm-ss  /  aaaa-mm-jj_hh-mm-ss  /  aaaa-mm-jj-hh-mm-ss
    - aaaammjj_hhmmss  /  aaaammjj-hhmmss  /  aaaammjj hhmmss
    - aaaa-mm-jj  /  aaaammjj
    """
    import re

    stem = Path(name).stem
    # Enlever suffixes de collision Pycture (_1, _2, …) — pas une heure type _143022
    stem = re.sub(r"_(?:\d{1,3})$", "", stem)

    patterns: list[tuple[str, str]] = [
        # 2005-08-15 14-30-22  |  2005-08-15_14-30-22  |  2005-08-15-14-30-22
        (
            r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[ _-](\d{2})[-_](\d{2})[-_](\d{2})(?!\d)",
            "%Y-%m-%d %H-%M-%S",
        ),
        # 20050815_143022  |  20050815-143022
        (
            r"(?<!\d)(\d{4})(\d{2})(\d{2})[_\- ](\d{2})(\d{2})(\d{2})(?!\d)",
            "%Y%m%d%H%M%S",
        ),
        # 2005-08-15
        (r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", "%Y-%m-%d"),
        # 20050815
        (r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", "%Y%m%d"),
    ]

    for pattern, _kind in patterns:
        m = re.search(pattern, stem)
        if not m:
            continue
        g = m.groups()
        try:
            if len(g) == 6:
                if "-" in pattern and "(\\d{4})-(\\d{2})-(\\d{2})" in pattern:
                    y, mo, d, h, mi, s = (int(x) for x in g)
                else:
                    y, mo, d, h, mi, s = (int(x) for x in g)
                return datetime(y, mo, d, h, mi, s)
            y, mo, d = (int(x) for x in g)
            return datetime(y, mo, d)
        except ValueError:
            continue
    return None


def _datetime_from_exif_ifd(exif) -> datetime | None:
    """Priorité : DateTimeOriginal → DateTimeDigitized (IFD Exif)."""
    if IFD is None:
        return None
    try:
        ifd = exif.get_ifd(IFD.Exif)
    except Exception:
        return None
    for tag_id in (_TAG_DATETIME_ORIGINAL, _TAG_DATETIME_DIGITIZED):
        parsed = _parse_exif_datetime(ifd.get(tag_id))
        if parsed:
            return parsed
    return None


def _datetime_from_getexif_flat(img: Image.Image) -> datetime | None:
    """Fallback via _getexif() (dict plat, ancien API Pillow)."""
    try:
        raw = img._getexif()  # noqa: SLF001
    except Exception:
        return None
    if not raw:
        return None
    # Priorité prise de vue, puis numérisation — JAMAIS DateTime en premier
    for tag_id in (_TAG_DATETIME_ORIGINAL, _TAG_DATETIME_DIGITIZED):
        parsed = _parse_exif_datetime(raw.get(tag_id))
        if parsed:
            return parsed
    return None


def _datetime_from_root_datetime(exif) -> datetime | None:
    """DateTime (306) : dernier recours EXIF (souvent date d'édition, pas de prise de vue)."""
    parsed = _parse_exif_datetime(exif.get(_TAG_DATETIME))
    if parsed:
        return parsed
    tagged = {TAGS.get(k, k): v for k, v in exif.items()}
    return _parse_exif_datetime(tagged.get("DateTime"))


def _compute_capture_info(path: Path) -> CaptureDate:
    """Calcul sans cache (EXIF / nom / mtime)."""
    if is_video(path):
        named = parse_datetime_from_filename(path.name)
        if named:
            return CaptureDate(named, "filename")
        return CaptureDate(_file_datetime(path), "mtime")

    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if exif:
                dt = _datetime_from_exif_ifd(exif)
                if dt:
                    try:
                        ifd = exif.get_ifd(IFD.Exif) if IFD else {}
                        if _parse_exif_datetime(ifd.get(_TAG_DATETIME_ORIGINAL)):
                            return CaptureDate(dt, "exif_original")
                        return CaptureDate(dt, "exif_digitized")
                    except Exception:
                        return CaptureDate(dt, "exif_original")

            dt = _datetime_from_getexif_flat(img)
            if dt:
                return CaptureDate(dt, "exif_original")
    except Exception:
        pass

    named = parse_datetime_from_filename(path.name)
    if named:
        return CaptureDate(named, "filename")

    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if exif:
                dt = _datetime_from_root_datetime(exif)
                if dt:
                    return CaptureDate(dt, "exif_datetime")
    except Exception:
        pass

    return CaptureDate(_file_datetime(path), "mtime")


def get_capture_info(path: Path, cache=None) -> CaptureDate:
    """
    Date de prise de vue + provenance (avec cache optionnel).

    Ordre :
    1. EXIF DateTimeOriginal
    2. EXIF DateTimeDigitized
    3. Date explicite dans le nom de fichier
    4. EXIF DateTime (tag 306, souvent date d'édition)
    5. Date de modification du fichier
    """
    if cache is not None:
        cached = cache.get_capture(path)
        if cached is not None:
            return cached
    capture = _compute_capture_info(path)
    if cache is not None:
        cache.put_capture(path, capture)
    return capture


def get_capture_datetime(path: Path, cache=None) -> datetime:
    """Date de prise de vue (compatibilité)."""
    return get_capture_info(path, cache=cache).value


def format_datetime_for_filename(dt: datetime) -> str:
    """Format aaaa-mm-jj hh-mm-ss pour les noms de fichiers."""
    return dt.strftime("%Y-%m-%d %H-%M-%S")


def set_file_datetime(path: Path, dt: datetime) -> None:
    """
    Aligne les dates filesystem sur la date de prise de vue.

    - atime / mtime : toujours (os.utime)
    - date de création (birthtime) sur macOS : SetFile si disponible ;
      sinon utime avec une date antérieure pousse souvent birthtime sur APFS
    """
    import os
    import subprocess
    import sys

    ts = dt.timestamp()
    os.utime(path, (ts, ts))

    if sys.platform != "darwin":
        return

    formatted = dt.strftime("%m/%d/%Y %H:%M:%S")
    try:
        subprocess.run(
            ["SetFile", "-d", formatted, "-m", formatted, str(path)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass
