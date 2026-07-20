"""Extraction des médias depuis une photothèque Apple Photos ou Aperture."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .exif_utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, is_junk_file

# Dossiers / fichiers à ignorer
_SKIP_NAMES = {".ds_store", "thumbs.db"}


@dataclass
class PhotosLibraryExportResult:
    library: Path
    destination: Path
    kind: str = ""  # "photos" | "aperture" | "photolibrary"
    copied: list[Path] = field(default_factory=list)
    skipped_missing: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        kind_label = {
            "photos": "Photos (.photoslibrary)",
            "aperture": "Aperture (.aplibrary)",
            "photolibrary": "iPhoto / Aperture (.photolibrary)",
        }.get(self.kind, self.kind or "?")
        return (
            f"Photothèque : {self.library.name} ({kind_label})\n"
            f"Destination : {self.destination}\n"
            f"Fichiers copiés : {len(self.copied)}\n"
            f"Absents / non téléchargés : {len(self.skipped_missing)}\n"
            f"Erreurs : {len(self.errors)}"
        )


def is_photos_library(path: Path) -> bool:
    """True si le chemin ressemble à une photothèque Photos (.photoslibrary)."""
    p = path.expanduser()
    if not p.exists() or not p.is_dir():
        return False
    if p.suffix.lower() != ".photoslibrary":
        return False
    return (p / "originals").is_dir() or (p / "Database").is_dir() or (p / "database").is_dir()


def _has_masters_layout(p: Path) -> bool:
    return (
        (p / "Masters").is_dir()
        or (p / "masters").is_dir()
        or (p / "Database").is_dir()
        or (p / "Aperture.aplib").is_dir()
    )


def is_aperture_library(path: Path) -> bool:
    """True pour une bibliothèque Aperture (.aplibrary)."""
    p = path.expanduser()
    if not p.exists() or not p.is_dir():
        return False
    if p.suffix.lower() != ".aplibrary":
        return False
    return _has_masters_layout(p)


def is_photolibrary(path: Path) -> bool:
    """True pour une bibliothèque iPhoto / Aperture (.photolibrary).

    Ne pas confondre avec Photos ``.photoslibrary`` (dossier originals/).
    """
    p = path.expanduser()
    if not p.exists() or not p.is_dir():
        return False
    if p.suffix.lower() != ".photolibrary":
        return False
    return _has_masters_layout(p)


def is_apple_media_library(path: Path) -> bool:
    """True pour Photos, Aperture (.aplibrary) ou iPhoto/Aperture (.photolibrary)."""
    return is_photos_library(path) or is_aperture_library(path) or is_photolibrary(path)


def library_kind(path: Path) -> str | None:
    if is_photos_library(path):
        return "photos"
    if is_aperture_library(path):
        return "aperture"
    if is_photolibrary(path):
        return "photolibrary"
    return None


def find_photos_sqlite(library: Path) -> Path | None:
    for rel in ("database/Photos.sqlite", "Database/Photos.sqlite"):
        candidate = library / rel
        if candidate.is_file():
            return candidate
    for p in library.rglob("Photos.sqlite"):
        if p.is_file():
            return p
    return None


def _load_original_filenames(db_path: Path) -> dict[str, str]:
    """
    Mappe UUID (minuscules, avec ou sans tirets) → nom de fichier d'origine.
    Schéma Photos variable selon les versions macOS.
    """
    mapping: dict[str, str] = {}
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return mapping

    queries = [
        """
        SELECT a.ZUUID, aa.ZORIGINALFILENAME
        FROM ZASSET a
        LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON aa.ZASSET = a.Z_PK
        WHERE a.ZUUID IS NOT NULL AND aa.ZORIGINALFILENAME IS NOT NULL
        """,
        """
        SELECT ZUUID, ZFILENAME FROM ZGENERICASSET
        WHERE ZUUID IS NOT NULL AND ZFILENAME IS NOT NULL
        """,
        """
        SELECT ZUUID, ZORIGINALFILENAME FROM ZADDITIONALASSETATTRIBUTES
        WHERE ZUUID IS NOT NULL AND ZORIGINALFILENAME IS NOT NULL
        """,
    ]

    try:
        cur = conn.cursor()
        for sql in queries:
            try:
                cur.execute(sql)
            except sqlite3.Error:
                continue
            for uuid_val, name in cur.fetchall():
                if not uuid_val or not name:
                    continue
                u = str(uuid_val).strip().lower()
                mapping[u] = str(name)
                mapping[u.replace("-", "")] = str(name)
            if mapping:
                break
    finally:
        conn.close()
    return mapping


def _uuid_from_originals_path(path: Path) -> str:
    """Extrait l'UUID depuis originals/X/UUID.ext."""
    return path.stem.lower()


def _iter_media_under(
    root: Path,
    *,
    include_videos: bool = True,
) -> list[Path]:
    if not root.is_dir():
        return []
    allowed = set(IMAGE_EXTENSIONS)
    if include_videos:
        allowed |= set(VIDEO_EXTENSIONS)
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file() or is_junk_file(p):
            continue
        if p.name.lower() in _SKIP_NAMES:
            continue
        if p.suffix.lower() in allowed:
            files.append(p)
    return sorted(files)


def iter_original_media(
    library: Path,
    *,
    include_videos: bool = True,
) -> list[Path]:
    """Liste les fichiers médias sous originals/ (Photos)."""
    return _iter_media_under(library / "originals", include_videos=include_videos)


def iter_aperture_masters(
    library: Path,
    *,
    include_videos: bool = True,
) -> list[Path]:
    """Liste les masters gérés sous Masters/ (Aperture).

    Les fichiers « référencés » (hors bibliothèque) ne sont pas dans Masters/
    et ne peuvent pas être exportés automatiquement.
    """
    for name in ("Masters", "masters"):
        root = library / name
        if root.is_dir():
            return _iter_media_under(root, include_videos=include_videos)
    return []


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _copy_media_list(
    media: list[Path],
    destination: Path,
    result: PhotosLibraryExportResult,
    *,
    name_for_src,
    progress_cb=None,
) -> None:
    total = len(media) or 1
    for i, src in enumerate(media):
        if progress_cb:
            progress_cb(i + 1, total, f"Export : {src.name}")

        try:
            if src.stat().st_size == 0:
                result.skipped_missing.append(src)
                continue
        except OSError as exc:
            result.errors.append((src, str(exc)))
            continue

        dest_name = name_for_src(src)
        dest = _unique_dest(destination / dest_name)
        try:
            shutil.copy2(src, dest)
            result.copied.append(dest)
        except OSError as exc:
            msg = str(exc)
            if "No such file" in msg or "Operation not permitted" in msg:
                result.skipped_missing.append(src)
            else:
                result.errors.append((src, msg))


def _export_photos_library(
    library: Path,
    destination: Path,
    *,
    include_videos: bool = True,
    progress_cb=None,
) -> PhotosLibraryExportResult:
    result = PhotosLibraryExportResult(
        library=library, destination=destination, kind="photos"
    )
    destination.mkdir(parents=True, exist_ok=True)

    name_map: dict[str, str] = {}
    db = find_photos_sqlite(library)
    if db is not None:
        try:
            tmp_db = destination / "._photos_tmp.sqlite"
            shutil.copy2(db, tmp_db)
            for suffix in ("-wal", "-shm"):
                side = Path(str(db) + suffix)
                if side.exists():
                    shutil.copy2(side, Path(str(tmp_db) + suffix))
            name_map = _load_original_filenames(tmp_db)
        except OSError:
            name_map = {}
        finally:
            for p in destination.glob("._photos_tmp.sqlite*"):
                try:
                    p.unlink()
                except OSError:
                    pass

    def name_for_src(src: Path) -> str:
        uuid = _uuid_from_originals_path(src)
        original_name = name_map.get(uuid) or name_map.get(uuid.replace("-", ""))
        if original_name:
            dest_name = Path(original_name).name
            if Path(dest_name).suffix.lower() != src.suffix.lower():
                dest_name = f"{Path(dest_name).stem}{src.suffix.lower()}"
            return dest_name
        return f"{uuid}{src.suffix.lower()}"

    media = iter_original_media(library, include_videos=include_videos)
    _copy_media_list(
        media, destination, result, name_for_src=name_for_src, progress_cb=progress_cb
    )
    return result


def _export_masters_library(
    library: Path,
    destination: Path,
    *,
    kind: str,
    include_videos: bool = True,
    progress_cb=None,
) -> PhotosLibraryExportResult:
    """Copie les masters gérés depuis Masters/ (Aperture / iPhoto .photolibrary)."""
    result = PhotosLibraryExportResult(
        library=library, destination=destination, kind=kind
    )
    destination.mkdir(parents=True, exist_ok=True)

    def name_for_src(src: Path) -> str:
        return src.name

    media = iter_aperture_masters(library, include_videos=include_videos)
    if not media:
        result.errors.append(
            (
                library,
                "Aucun master trouvé sous Masters/. "
                "Les fichiers « référencés » (hors bibliothèque) ne sont pas exportables "
                "automatiquement — consolidez-les ou exportez-les depuis Aperture / iPhoto.",
            )
        )
        return result

    _copy_media_list(
        media, destination, result, name_for_src=name_for_src, progress_cb=progress_cb
    )
    return result


def export_photos_library(
    library: Path,
    destination: Path,
    *,
    include_videos: bool = True,
    progress_cb=None,
) -> PhotosLibraryExportResult:
    """
    Copie les originaux d'une photothèque Apple vers destination.

    Accepte :
    - Photos : ``Nom.photoslibrary`` (dossier ``originals/``)
    - Aperture : ``Nom.aplibrary`` (dossier ``Masters/``)
    - iPhoto / Aperture : ``Nom.photolibrary`` (dossier ``Masters/``)

    Ne modifie jamais la photothèque.
    """
    library = library.expanduser().resolve()
    destination = destination.expanduser().resolve()
    kind = library_kind(library)
    if kind == "photos":
        return _export_photos_library(
            library,
            destination,
            include_videos=include_videos,
            progress_cb=progress_cb,
        )
    if kind in ("aperture", "photolibrary"):
        return _export_masters_library(
            library,
            destination,
            kind=kind,
            include_videos=include_videos,
            progress_cb=progress_cb,
        )

    result = PhotosLibraryExportResult(library=library, destination=destination)
    result.errors.append(
        (
            library,
            "Ce n'est pas une photothèque valide "
            "(.photoslibrary / .aplibrary / .photolibrary)",
        )
    )
    return result
