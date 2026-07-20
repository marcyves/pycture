"""Cache SQLite local (.pycture/cache.sqlite) : empreintes + dates de capture."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .exif_utils import CaptureDate

CACHE_DIR_NAME = ".pycture"
CACHE_DB_NAME = "cache.sqlite"


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0

    @property
    def summary(self) -> str:
        return f"Cache : {self.hits} hits / {self.misses} miss"


def cache_dir(root: Path) -> Path:
    return root.resolve() / CACHE_DIR_NAME


def cache_db_path(root: Path) -> Path:
    return cache_dir(root) / CACHE_DB_NAME


def is_under_pycture_meta(path: Path) -> bool:
    """True si le chemin est dans un dossier .pycture (métadonnées Pycture)."""
    return CACHE_DIR_NAME in path.parts


def clear_folder_cache(root: Path) -> bool:
    """Supprime le cache SQLite du dossier. Retourne True si quelque chose a été effacé."""
    db = cache_db_path(root)
    removed = False
    if db.is_file():
        db.unlink()
        removed = True
    # Supprimer aussi les fichiers WAL/SHM éventuels
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.is_file():
            side.unlink()
            removed = True
    meta = cache_dir(root)
    if meta.is_dir() and not any(meta.iterdir()):
        meta.rmdir()
    return removed


class FolderCache:
    """
    Cache par arborescence. Invalidation : size + mtime_ns.

    Si le dossier n'est pas accessible en écriture, ``open`` retourne un cache
    désactivé (toujours miss, pas d'écriture).
    """

    def __init__(
        self,
        root: Path,
        conn: sqlite3.Connection | None,
        *,
        enabled: bool = True,
    ) -> None:
        self.root = root.resolve()
        self._conn = conn
        self.enabled = enabled and conn is not None
        self.stats = CacheStats()

    @classmethod
    def open(cls, root: Path) -> FolderCache:
        root = root.resolve()
        if not root.is_dir():
            return cls(root, None, enabled=False)
        try:
            meta = cache_dir(root)
            meta.mkdir(parents=True, exist_ok=True)
            db = meta / CACHE_DB_NAME
            conn = sqlite3.connect(str(db), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_cache (
                  rel_path TEXT PRIMARY KEY,
                  size INTEGER NOT NULL,
                  mtime_ns INTEGER NOT NULL,
                  digest TEXT,
                  capture_iso TEXT,
                  capture_source TEXT,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_file_cache_digest
                  ON file_cache(digest);
                """
            )
            conn.commit()
            return cls(root, conn, enabled=True)
        except OSError:
            return cls(root, None, enabled=False)
        except sqlite3.Error:
            return cls(root, None, enabled=False)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> FolderCache:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _rel_key(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return None

    def _stat_pair(self, path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
            mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            return int(st.st_size), int(mtime_ns)
        except OSError:
            return None

    def _fetch_valid_row(self, path: Path) -> sqlite3.Row | None:
        if not self.enabled or self._conn is None:
            return None
        rel = self._rel_key(path)
        stats = self._stat_pair(path)
        if rel is None or stats is None:
            return None
        size, mtime_ns = stats
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute(
            "SELECT * FROM file_cache WHERE rel_path = ?",
            (rel,),
        ).fetchone()
        if row is None:
            return None
        if int(row["size"]) != size or int(row["mtime_ns"]) != mtime_ns:
            return None
        return row

    def get_digest(self, path: Path) -> str | None:
        if not self.enabled:
            return None
        row = self._fetch_valid_row(path)
        if row is None:
            self.stats.misses += 1
            return None
        digest = row["digest"]
        if not digest:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return str(digest)

    def put_digest(self, path: Path, digest: str) -> None:
        self._upsert(path, digest=digest)

    def get_capture(self, path: Path) -> CaptureDate | None:
        from .exif_utils import CaptureDate

        if not self.enabled:
            return None
        row = self._fetch_valid_row(path)
        if row is None:
            self.stats.misses += 1
            return None
        iso = row["capture_iso"]
        source = row["capture_source"]
        if not iso or not source:
            self.stats.misses += 1
            return None
        try:
            value = datetime.fromisoformat(iso)
            self.stats.hits += 1
            return CaptureDate(value, source)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            self.stats.misses += 1
            return None

    def put_capture(self, path: Path, capture: CaptureDate) -> None:
        self._upsert(
            path,
            capture_iso=capture.value.isoformat(),
            capture_source=capture.source,
        )

    def _upsert(
        self,
        path: Path,
        *,
        digest: str | None = None,
        capture_iso: str | None = None,
        capture_source: str | None = None,
    ) -> None:
        if not self.enabled or self._conn is None:
            return
        rel = self._rel_key(path)
        stats = self._stat_pair(path)
        if rel is None or stats is None:
            return
        size, mtime_ns = stats
        now = datetime.now(timezone.utc).isoformat()
        try:
            row = self._conn.execute(
                "SELECT digest, capture_iso, capture_source FROM file_cache WHERE rel_path = ?",
                (rel,),
            ).fetchone()
            merged_digest = digest if digest is not None else (row[0] if row else None)
            merged_iso = capture_iso if capture_iso is not None else (row[1] if row else None)
            merged_src = (
                capture_source if capture_source is not None else (row[2] if row else None)
            )
            self._conn.execute(
                """
                INSERT INTO file_cache (
                  rel_path, size, mtime_ns, digest, capture_iso, capture_source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                  size = excluded.size,
                  mtime_ns = excluded.mtime_ns,
                  digest = excluded.digest,
                  capture_iso = excluded.capture_iso,
                  capture_source = excluded.capture_source,
                  updated_at = excluded.updated_at
                """,
                (rel, size, mtime_ns, merged_digest, merged_iso, merged_src, now),
            )
            self._conn.commit()
        except sqlite3.Error:
            pass

    def purge_missing(self) -> int:
        """Supprime les entrées dont le fichier n'existe plus. Retourne le nombre retiré."""
        if not self.enabled or self._conn is None:
            return 0
        rows = self._conn.execute("SELECT rel_path FROM file_cache").fetchall()
        removed = 0
        for (rel,) in rows:
            if not (self.root / rel).is_file():
                self._conn.execute("DELETE FROM file_cache WHERE rel_path = ?", (rel,))
                removed += 1
        if removed:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
        return removed
