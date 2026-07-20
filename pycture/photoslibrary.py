"""Extraction des médias depuis une photothèque Apple (.photoslibrary)."""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .exif_utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, is_junk_file

# Dossiers / fichiers à ignorer dans originals/
_SKIP_NAMES = {".ds_store", "thumbs.db"}


@dataclass
class PhotosLibraryExportResult:
    library: Path
    destination: Path
    copied: list[Path] = field(default_factory=list)
    skipped_missing: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"Photothèque : {self.library.name}\n"
            f"Destination : {self.destination}\n"
            f"Fichiers copiés : {len(self.copied)}\n"
            f"Absents / non téléchargés (iCloud) : {len(self.skipped_missing)}\n"
            f"Erreurs : {len(self.errors)}"
        )


def is_photos_library(path: Path) -> bool:
    """True si le chemin ressemble à une photothèque Photos."""
    p = path.expanduser()
    if not p.exists():
        return False
    if p.suffix.lower() != ".photoslibrary":
        return False
    # Package = dossier
    if not p.is_dir():
        return False
    return (p / "originals").is_dir() or (p / "Database").is_dir() or (p / "database").is_dir()


def find_photos_sqlite(library: Path) -> Path | None:
    for rel in ("database/Photos.sqlite", "Database/Photos.sqlite"):
        candidate = library / rel
        if candidate.is_file():
            return candidate
    # Fallback
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
        # Catalina+ courant
        """
        SELECT a.ZUUID, aa.ZORIGINALFILENAME
        FROM ZASSET a
        LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON aa.ZASSET = a.Z_PK
        WHERE a.ZUUID IS NOT NULL AND aa.ZORIGINALFILENAME IS NOT NULL
        """,
        # Variante ancienne
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


def iter_original_media(
    library: Path,
    *,
    include_videos: bool = True,
) -> list[Path]:
    """Liste les fichiers médias sous originals/."""
    originals = library / "originals"
    if not originals.is_dir():
        return []

    allowed = set(IMAGE_EXTENSIONS)
    if include_videos:
        allowed |= set(VIDEO_EXTENSIONS)

    files: list[Path] = []
    for p in originals.rglob("*"):
        if not p.is_file() or is_junk_file(p):
            continue
        if p.name.lower() in _SKIP_NAMES:
            continue
        if p.suffix.lower() in allowed:
            files.append(p)
    return sorted(files)


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


def export_photos_library(
    library: Path,
    destination: Path,
    *,
    include_videos: bool = True,
    progress_cb=None,
) -> PhotosLibraryExportResult:
    """
    Copie les originaux d'une .photoslibrary vers destination.

    - Ne modifie jamais la photothèque.
    - Utilise le nom d'origine issu de Photos.sqlite quand disponible.
    - Les fichiers absents (iCloud non téléchargé) sont signalés si la taille est 0
      ou si la copie échoue.
    """
    library = library.expanduser().resolve()
    destination = destination.expanduser().resolve()
    result = PhotosLibraryExportResult(library=library, destination=destination)

    if not is_photos_library(library):
        result.errors.append((library, "Ce n'est pas une photothèque Photos valide (.photoslibrary)"))
        return result

    destination.mkdir(parents=True, exist_ok=True)

    name_map: dict[str, str] = {}
    db = find_photos_sqlite(library)
    if db is not None:
        try:
            # Copie temporaire pour éviter les locks Photos.app
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

    media = iter_original_media(library, include_videos=include_videos)
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

        uuid = _uuid_from_originals_path(src)
        original_name = name_map.get(uuid) or name_map.get(uuid.replace("-", ""))
        if original_name:
            dest_name = Path(original_name).name
            # Garder l'extension réelle du fichier si le nom DB diffère
            if Path(dest_name).suffix.lower() != src.suffix.lower():
                dest_name = f"{Path(dest_name).stem}{src.suffix.lower()}"
        else:
            dest_name = f"{uuid}{src.suffix.lower()}"

        dest = _unique_dest(destination / dest_name)
        try:
            shutil.copy2(src, dest)
            result.copied.append(dest)
        except OSError as exc:
            # Souvent : fichier iCloud non matérialisé
            msg = str(exc)
            if "No such file" in msg or "Operation not permitted" in msg:
                result.skipped_missing.append(src)
            else:
                result.errors.append((src, msg))

    return result
