"""Extraction de la date de prise de vue depuis les métadonnées EXIF."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS

try:
    from PIL.ExifTags import IFD
except ImportError:  # pragma: no cover
    IFD = None  # type: ignore[misc, assignment]

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


def get_capture_datetime(path: Path) -> datetime:
    """
    Date de prise de vue.

    Ordre strict :
    1. EXIF DateTimeOriginal
    2. EXIF DateTimeDigitized
    3. EXIF DateTime (tag 306, souvent incorrect)
    4. Date de modification du fichier
    """
    if is_video(path):
        return _file_datetime(path)

    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if exif:
                dt = _datetime_from_exif_ifd(exif)
                if dt:
                    return dt

            dt = _datetime_from_getexif_flat(img)
            if dt:
                return dt

            if exif:
                dt = _datetime_from_root_datetime(exif)
                if dt:
                    return dt
    except Exception:
        pass

    return _file_datetime(path)


def format_datetime_for_filename(dt: datetime) -> str:
    """Format aaaa-mm-jj hh-mm-ss pour les noms de fichiers."""
    return dt.strftime("%Y-%m-%d %H-%M-%S")
