"""Fusion de dossiers média sans doublons ni écrasement."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .duplicates import file_digest
from .folder_cache import FolderCache
from .organizer import collect_media, unique_destination


@dataclass
class MergeOptions:
    source_dir: Path
    destination_dir: Path
    move: bool = False
    include_videos: bool = True


@dataclass
class PlannedMerge:
    source: Path
    destination: Path
    reason: str = "merge"  # merge | rename_conflict | skip_duplicate


@dataclass
class MergePlan:
    actions: list[PlannedMerge] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    move: bool = False
    cache_hits: int = 0
    cache_misses: int = 0

    @property
    def to_merge(self) -> list[PlannedMerge]:
        return [a for a in self.actions if a.reason in ("merge", "rename_conflict")]

    @property
    def skipped(self) -> list[PlannedMerge]:
        return [a for a in self.actions if a.reason == "skip_duplicate"]

    @property
    def renames(self) -> list[PlannedMerge]:
        return [a for a in self.actions if a.reason == "rename_conflict"]

    @property
    def summary(self) -> str:
        lines = [
            f"À fusionner : {len(self.to_merge)}",
            f"  dont renommages conflit : {len(self.renames)}",
            f"Ignorés (doublon contenu) : {len(self.skipped)}",
            f"Erreurs : {len(self.errors)}",
            f"Mode : {'déplacer' if self.move else 'copier'}",
        ]
        if self.cache_hits or self.cache_misses:
            lines.append(f"Cache : {self.cache_hits} hits / {self.cache_misses} miss")
        return "\n".join(lines)


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _index_destination_digests(
    destination: Path,
    include_videos: bool,
    progress_cb=None,
    cache=None,
) -> dict[str, Path]:
    """SHA-256 → chemin pour tous les médias déjà présents sous Destination."""
    digests: dict[str, Path] = {}
    if not destination.is_dir():
        return digests
    media = collect_media(destination, include_videos=include_videos)
    total = len(media) or 1
    for i, path in enumerate(media):
        if progress_cb:
            progress_cb(i + 1, total, f"Empreinte dest : {path.name}")
        try:
            digests[file_digest(path, cache=cache)] = path
        except OSError:
            continue
    return digests


def build_merge_plan(options: MergeOptions, progress_cb=None) -> MergePlan:
    """Analyse Source → Destination et construit un plan (sans écrire)."""
    plan = MergePlan(move=options.move)
    source = options.source_dir.resolve()
    destination = options.destination_dir.resolve()

    if not source.is_dir():
        plan.errors.append((source, "Dossier source introuvable"))
        return plan
    if not destination.is_dir():
        plan.errors.append((destination, "Dossier destination introuvable"))
        return plan
    if _path_key(source) == _path_key(destination):
        plan.errors.append((source, "Source et destination sont le même dossier"))
        return plan

    src_cache = FolderCache.open(source)
    dst_cache = FolderCache.open(destination)
    try:
        known_digests = _index_destination_digests(
            destination,
            include_videos=options.include_videos,
            progress_cb=progress_cb,
            cache=dst_cache,
        )
        reserved_dests: set[str] = set()

        media = collect_media(source, include_videos=options.include_videos)
        # Ne pas re-traiter des fichiers déjà sous Destination (source englobante)
        media = [p for p in media if not _is_under(p, destination)]
        total = len(media) or 1

        for i, path in enumerate(media):
            if progress_cb:
                progress_cb(i + 1, total, f"Analyse fusion : {path.name}")

            try:
                digest = file_digest(path, cache=src_cache)
            except OSError as exc:
                plan.errors.append((path, str(exc)))
                continue

            if digest in known_digests:
                plan.actions.append(
                    PlannedMerge(
                        source=path,
                        destination=known_digests[digest],
                        reason="skip_duplicate",
                    )
                )
                continue

            try:
                rel = path.relative_to(source)
            except ValueError:
                plan.errors.append((path, "Hors du dossier source"))
                continue

            dest_cand = destination / rel
            needs_rename = dest_cand.exists() or _path_key(dest_cand) in reserved_dests
            if needs_rename:
                # Contenu différent (sinon déjà filtré par digest) ou collision planifiée
                dest = unique_destination(dest_cand, reserved=reserved_dests)
                reason = "rename_conflict"
            else:
                dest = dest_cand
                reason = "merge"

            plan.actions.append(
                PlannedMerge(source=path, destination=dest, reason=reason)
            )
            known_digests[digest] = dest
            reserved_dests.add(_path_key(dest))
            reserved_dests.add(str(dest).casefold())

        src_cache.purge_missing()
        dst_cache.purge_missing()
        plan.cache_hits = src_cache.stats.hits + dst_cache.stats.hits
        plan.cache_misses = src_cache.stats.misses + dst_cache.stats.misses
        return plan
    finally:
        src_cache.close()
        dst_cache.close()


def execute_merge_plan(
    plan: MergePlan,
    dry_run: bool = True,
    progress_cb=None,
) -> list[str]:
    """Exécute le plan (copie ou déplacement). Retourne des messages de log."""
    logs: list[str] = []
    verb = "DÉPLACER" if plan.move else "COPIER"
    total = len(plan.actions) or 1

    for i, action in enumerate(plan.actions):
        if progress_cb:
            progress_cb(i + 1, total, action.source.name)

        if action.reason == "skip_duplicate":
            logs.append(
                f"IGNORER (doublon) {action.source} ≡ {action.destination}"
            )
            continue

        if action.reason == "rename_conflict":
            logs.append(f"RENOMMER {action.source} → {action.destination}")
        else:
            logs.append(f"{verb} {action.source} → {action.destination}")

        if dry_run:
            continue

        try:
            if not action.source.is_file():
                logs.append("  ERREUR : source introuvable")
                continue
            action.destination.parent.mkdir(parents=True, exist_ok=True)
            dest = action.destination
            if dest.exists():
                dest = unique_destination(dest)
                if dest != action.destination:
                    logs.append(f"  (renommé en {dest.name} pour éviter un conflit)")
            if plan.move:
                shutil.move(str(action.source), str(dest))
            else:
                shutil.copy2(str(action.source), str(dest))
        except OSError as exc:
            logs.append(f"  ERREUR : {exc}")

    return logs
