"""Détection et gestion des doublons d'images."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DuplicateGroup:
    """Groupe de fichiers identiques (même empreinte)."""

    digest: str
    paths: list[Path] = field(default_factory=list)

    @property
    def keeper(self) -> Path:
        """Conserve le fichier le plus ancien (mtime), à égalité le chemin le plus court."""
        return min(self.paths, key=lambda p: (p.stat().st_mtime, len(str(p)), str(p)))

    @property
    def duplicates(self) -> list[Path]:
        keep = self.keeper
        return [p for p in self.paths if p != keep]


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

    return [
        DuplicateGroup(digest=d, paths=list(ps))
        for d, ps in by_hash.items()
        if len(ps) > 1
    ]
