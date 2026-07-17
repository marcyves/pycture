"""Extraction de la date de prise de vue depuis les métadonnées EXIF."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS

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
    """Date de création du fichier si dispo, sinon date de modification."""
    stat = path.stat()
    # macOS / BSD : st_birthtime
    birth = getattr(stat, "st_birthtime", None)
    if birth:
        return datetime.fromtimestamp(birth)
    return datetime.fromtimestamp(stat.st_mtime)


# Tags EXIF courants pour la date de prise de vue
_DATE_TAGS = (
    "DateTimeOriginal",
    "DateTimeDigitized",
    "DateTime",
)


def _parse_exif_datetime(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def get_capture_datetime(path: Path) -> datetime:
    """Date de prise de vue EXIF pour les images ; date fichier pour les vidéos."""
    if is_video(path):
        return _file_datetime(path)

    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if exif:
                tagged = {TAGS.get(k, k): v for k, v in exif.items()}
                for tag in _DATE_TAGS:
                    raw = tagged.get(tag)
                    if isinstance(raw, str):
                        parsed = _parse_exif_datetime(raw)
                        if parsed:
                            return parsed

                # EXIF IFD (Pillow >= 8)
                try:
                    from PIL.ExifTags import IFD

                    ifd = exif.get_ifd(IFD.Exif)
                    for tag_id, name in (
                        (36867, "DateTimeOriginal"),
                        (36868, "DateTimeDigitized"),
                    ):
                        raw = ifd.get(tag_id)
                        if isinstance(raw, str):
                            parsed = _parse_exif_datetime(raw)
                            if parsed:
                                return parsed
                except Exception:
                    pass
    except Exception:
        pass

    return _file_datetime(path)


def format_datetime_for_filename(dt: datetime) -> str:
    """Format aaaa-mm-jj hh-mm-ss pour les noms de fichiers."""
    return dt.strftime("%Y-%m-%d %H-%M-%S")
