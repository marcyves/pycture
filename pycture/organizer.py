"""Organisation des photos : renommage, structure de dossiers, doublons."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .duplicates import DuplicateGroup, find_duplicates
from .exif_utils import (
    format_datetime_for_filename,
    get_capture_datetime,
    is_image,
    is_junk_file,
)


class FolderStructure(str, Enum):
    YEAR_MONTH_DAY = "year_month_day"
    YEAR_MONTH_EVENT = "year_month_event"
    YEAR_EVENT = "year_event"


class DuplicateAction(str, Enum):
    KEEP_BOTH = "keep_both"
    MOVE_TO_DOUBLONS = "move_to_doublons"
    DELETE = "delete"


@dataclass
class OrganizerOptions:
    source_dir: Path
    structure: FolderStructure = FolderStructure.YEAR_MONTH_DAY
    event_name: str = ""
    rename_with_datetime: bool = True
    duplicate_action: DuplicateAction = DuplicateAction.MOVE_TO_DOUBLONS
    dry_run: bool = True
    output_dir: Path | None = None  # None = réorganiser dans le dossier source
    clean_junk: bool = True  # supprimer ._* / .DS_Store etc.


@dataclass
class PlannedMove:
    source: Path
    destination: Path
    reason: str = ""


@dataclass
class OrganizerPlan:
    moves: list[PlannedMove] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    junk_files: list[Path] = field(default_factory=list)

    @property
    def summary(self) -> str:
        n_dupes = sum(len(g.duplicates) for g in self.duplicate_groups)
        n_org = sum(1 for m in self.moves if m.reason in ("organisation", "doublon", "suppression"))
        lines = [
            f"Images à déplacer / renommer : {n_org}",
            f"Groupes de doublons : {len(self.duplicate_groups)} ({n_dupes} fichiers en trop)",
            f"Fichiers parasites (._* / système) : {len(self.junk_files)}",
            f"Ignorés : {len(self.skipped)}",
            f"Erreurs : {len(self.errors)}",
        ]
        return "\n".join(lines)


def collect_images(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if is_image(p))


def collect_junk_files(root: Path) -> list[Path]:
    """Fichiers macOS AppleDouble (._*) et autres parasites système."""
    return sorted(p for p in root.rglob("*") if p.is_file() and is_junk_file(p))


def _sanitize_name(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if c in forbidden else c for c in name.strip())
    return cleaned.strip(" .") or "sans_nom"


def _target_folder(dt, options: OrganizerOptions, base: Path) -> Path:
    year = f"{dt.year:04d}"
    month = f"{dt.month:02d}"
    day = f"{dt.day:02d}"
    event = _sanitize_name(options.event_name) if options.event_name else "Divers"

    if options.structure == FolderStructure.YEAR_MONTH_DAY:
        return base / year / month / day
    if options.structure == FolderStructure.YEAR_MONTH_EVENT:
        return base / year / month / event
    return base / year / event


def _unique_destination(dest: Path) -> Path:
    """Évite d'écraser un fichier existant en ajoutant _1, _2, …"""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    parent = dest.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def build_plan(options: OrganizerOptions, progress_cb=None) -> OrganizerPlan:
    """Analyse le dossier et construit un plan d'actions (sans écrire sur le disque)."""
    plan = OrganizerPlan()
    source = options.source_dir.resolve()
    if not source.is_dir():
        plan.errors.append((source, "Dossier introuvable"))
        return plan

    base = (options.output_dir or source).resolve()
    images = collect_images(source)

    if progress_cb:
        progress_cb(0, max(len(images), 1), "Recherche des doublons…")

    plan.duplicate_groups = find_duplicates(images, progress_cb=progress_cb)

    # Fichiers à exclure du déplacement principal (doublons non conservés)
    excluded: set[Path] = set()
    if options.duplicate_action != DuplicateAction.KEEP_BOTH:
        for group in plan.duplicate_groups:
            for dup in group.duplicates:
                excluded.add(dup.resolve())

    total = len(images) or 1
    used_names: dict[Path, set[str]] = {}

    for i, path in enumerate(images):
        if progress_cb:
            progress_cb(i + 1, total, f"Analyse : {path.name}")

        resolved = path.resolve()
        if resolved in excluded:
            continue

        try:
            dt = get_capture_datetime(path)
            folder = _target_folder(dt, options, base)

            if options.rename_with_datetime:
                new_name = f"{format_datetime_for_filename(dt)}{path.suffix.lower()}"
            else:
                new_name = path.name

            # Unicité dans le plan (avant écriture disque)
            names = used_names.setdefault(folder, set())
            candidate = folder / new_name
            if new_name in names or (candidate.exists() and candidate.resolve() != resolved):
                stem = Path(new_name).stem
                suffix = Path(new_name).suffix
                n = 1
                while True:
                    alt = f"{stem}_{n}{suffix}"
                    if alt not in names and not (folder / alt).exists():
                        new_name = alt
                        break
                    n += 1
            names.add(new_name)

            dest = folder / new_name
            if dest.resolve() == resolved:
                plan.skipped.append((path, "Déjà à la bonne place"))
                continue

            plan.moves.append(PlannedMove(source=path, destination=dest, reason="organisation"))
        except Exception as exc:
            plan.errors.append((path, str(exc)))

    # Doublons : déplacement vers _doublons ou suppression
    if options.duplicate_action == DuplicateAction.MOVE_TO_DOUBLONS:
        doublons_dir = base / "_doublons"
        for group in plan.duplicate_groups:
            for dup in group.duplicates:
                dest = _unique_destination(doublons_dir / dup.name)
                plan.moves.append(
                    PlannedMove(source=dup, destination=dest, reason="doublon")
                )
    elif options.duplicate_action == DuplicateAction.DELETE:
        for group in plan.duplicate_groups:
            for dup in group.duplicates:
                plan.moves.append(
                    PlannedMove(source=dup, destination=Path(), reason="suppression")
                )

    # Fichiers parasites macOS (._* , .DS_Store, …)
    if options.clean_junk:
        # Chercher dans source et destination éventuelle
        roots = {source}
        if options.output_dir:
            out = options.output_dir.resolve()
            if out.is_dir():
                roots.add(out)
        junk: list[Path] = []
        for root in roots:
            junk.extend(collect_junk_files(root))
        # Dédupliquer
        seen: set[Path] = set()
        for j in junk:
            rj = j.resolve()
            if rj in seen:
                continue
            seen.add(rj)
            plan.junk_files.append(j)
            plan.moves.append(
                PlannedMove(source=j, destination=Path(), reason="junk")
            )

    return plan


def _remove_appledouble_sidecar(path: Path) -> None:
    """Supprime le fichier ._<nom> associé s'il existe (métadonnées macOS)."""
    sidecar = path.parent / f"._{path.name}"
    try:
        if sidecar.is_file():
            sidecar.unlink()
    except OSError:
        pass


def execute_plan(plan: OrganizerPlan, dry_run: bool = True, progress_cb=None) -> list[str]:
    """Exécute le plan. Retourne une liste de messages de log."""
    logs: list[str] = []
    total = len(plan.moves) or 1

    for i, move in enumerate(plan.moves):
        if progress_cb:
            progress_cb(i + 1, total, f"{move.source.name}")

        if move.reason in ("suppression", "junk"):
            label = "PARASITE" if move.reason == "junk" else "SUPPRIMER"
            msg = f"{label} {move.source}"
            logs.append(msg)
            if not dry_run:
                try:
                    move.source.unlink()
                except OSError as exc:
                    logs.append(f"  ERREUR : {exc}")
            continue

        msg = f"{move.source} → {move.destination}"
        logs.append(msg)
        if dry_run:
            continue

        try:
            move.destination.parent.mkdir(parents=True, exist_ok=True)
            dest = _unique_destination(move.destination)
            # Nettoyer les sidecars avant/après déplacement
            _remove_appledouble_sidecar(move.source)
            shutil.move(str(move.source), str(dest))
            _remove_appledouble_sidecar(dest)
            if dest != move.destination:
                logs.append(f"  (renommé en {dest.name} pour éviter un conflit)")
        except OSError as exc:
            logs.append(f"  ERREUR : {exc}")

    return logs


def remove_empty_dirs(root: Path, dry_run: bool = True) -> list[Path]:
    """Supprime les dossiers vides sous root (sauf root lui-même)."""
    removed: list[Path] = []
    # Du plus profond au plus haut
    dirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in dirs:
        if d.resolve() == root.resolve():
            continue
        try:
            if any(d.iterdir()):
                continue
            removed.append(d)
            if not dry_run:
                d.rmdir()
        except OSError:
            continue
    return removed
