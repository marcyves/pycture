"""Détection et gestion des doublons d'images."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Noms déjà renommés par Pycture : aaaa-mm-jj hh-mm-ss
_DATETIME_NAME = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}(?:_\d+)?$",
    re.IGNORECASE,
)


def _looks_datetime_named(path: Path) -> bool:
    return bool(_DATETIME_NAME.match(path.stem))


def _keeper_sort_key(path: Path) -> tuple:
    """
    Préfère : original (pas déjà renommé) > plus ancien mtime > chemin plus court.
    Ainsi une copie déjà organisée est plutôt traitée comme doublon.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = float("inf")
    return (
        1 if _looks_datetime_named(path) else 0,
        mtime,
        len(str(path)),
        str(path).casefold(),
    )


@dataclass
class DuplicateGroup:
    """Groupe de fichiers identiques (même empreinte)."""

    digest: str
    paths: list[Path] = field(default_factory=list)
    keeper: Path | None = None

    def __post_init__(self) -> None:
        existing = [p for p in self.paths if p.is_file()]
        self.paths = existing
        if not self.paths:
            self.keeper = Path()
            return
        if self.keeper is None or self.keeper not in self.paths:
            self.keeper = min(self.paths, key=_keeper_sort_key)

    @property
    def duplicates(self) -> list[Path]:
        keep = self.keeper
        return [p for p in self.paths if p.resolve() != keep.resolve()]


def file_digest(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Empreinte SHA-256 du contenu du fichier."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(paths: list[Path], progress_cb=None) -> list[DuplicateGroup]:
    """
    Regroupe les fichiers par empreinte.
    Ne retourne que les groupes avec au moins 2 fichiers.
    """
    by_size: dict[int, list[Path]] = defaultdict(list)
    for p in paths:
        try:
            by_size[p.stat().st_size].append(p)
        except OSError:
            continue

    candidates = [p for group in by_size.values() if len(group) > 1 for p in group]
    total = len(candidates) or 1
    by_hash: dict[str, list[Path]] = defaultdict(list)

    for i, path in enumerate(candidates):
        if progress_cb:
            progress_cb(i + 1, total, f"Empreinte : {path.name}")
        try:
            digest = file_digest(path)
            by_hash[digest].append(path)
        except OSError:
            continue

    # Dédupliquer les chemins qui pointent vers le même inode / même resolve
    groups: list[DuplicateGroup] = []
    for digest, ps in by_hash.items():
        unique: list[Path] = []
        seen: set[str] = set()
        for p in ps:
            try:
                key = str(p.resolve()).casefold()
            except OSError:
                key = str(p).casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        if len(unique) > 1:
            groups.append(DuplicateGroup(digest=digest, paths=unique))
    return groups
