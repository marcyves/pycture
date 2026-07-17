"""Organisation des photos : renommage, structure de dossiers, doublons."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .duplicates import DuplicateGroup, find_duplicates
from .exif_utils import (
    format_datetime_for_filename,
    get_capture_info,
    is_image,
    is_junk_file,
    is_video,
    set_file_datetime,
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
    include_videos: bool = True  # AVI etc. → année/video
    sync_file_dates: bool = True  # aligner mtime/création sur EXIF


@dataclass
class PlannedMove:
    source: Path
    destination: Path
    reason: str = ""
    capture_dt: datetime | None = None


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
        n_photos = sum(1 for m in self.moves if m.reason == "organisation")
        n_videos = sum(1 for m in self.moves if m.reason == "video")
        n_sans = sum(1 for m in self.moves if m.reason == "sans_exif")
        n_sync = sum(1 for m in self.moves if m.reason == "sync_dates")
        n_dup_actions = sum(1 for m in self.moves if m.reason in ("doublon", "suppression"))
        lines = [
            f"Photos à déplacer / renommer : {n_photos}",
            f"Sans EXIF (→ année/_sans_exif) : {n_sans}",
            f"Vidéos → année/video : {n_videos}",
            f"Dates fichier à aligner sur EXIF : {n_sync}",
            f"Actions doublons : {n_dup_actions} ({len(self.duplicate_groups)} groupes, {n_dupes} en trop)",
            f"Fichiers parasites (._* / système) : {len(self.junk_files)}",
            f"Ignorés : {len(self.skipped)}",
            f"Erreurs : {len(self.errors)}",
        ]
        return "\n".join(lines)


def collect_images(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if is_image(p))


def collect_videos(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if is_video(p))


def collect_media(root: Path, include_videos: bool = True) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if is_image(p) or (include_videos and is_video(p))
    )


def collect_junk_files(root: Path) -> list[Path]:
    """Fichiers macOS AppleDouble (._*) et autres parasites système."""
    return sorted(p for p in root.rglob("*") if p.is_file() and is_junk_file(p))


def _sanitize_name(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if c in forbidden else c for c in name.strip())
    return cleaned.strip(" .") or "sans_nom"


def organization_base(source_dir: Path, output_dir: Path | None) -> Path:
    """
    Racine où créer année/mois/jour.

    Si le dossier de travail s'appelle déjà une année (ex. .../Photos/2003),
    on organise dans le parent (.../Photos) pour éviter .../2003/2003/09/10.
    """
    if output_dir is not None:
        return output_dir.resolve()
    source = source_dir.resolve()
    if source.name.isdigit() and len(source.name) == 4:
        return source.parent
    return source


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve().as_posix().casefold() == b.resolve().as_posix().casefold()
    except OSError:
        return str(a).casefold() == str(b).casefold()


def _source_year_hint(source_dir: Path) -> int | None:
    """Si le dossier de travail s'appelle YYYY, retourne cette année."""
    name = source_dir.resolve().name
    if name.isdigit() and len(name) == 4:
        year = int(name)
        if 1980 <= year <= 2100:
            return year
    return None


def _target_folder(
    dt,
    options: OrganizerOptions,
    base: Path,
    *,
    video: bool = False,
    no_exif: bool = False,
    year_hint: int | None = None,
) -> Path:
    """
    Sans EXIF fiable et avec un dossier année source : année/_sans_exif
    (évite d'envoyer des scans/exports vers l'année de la date fichier, ex. 2022).
    """
    if no_exif and year_hint is not None and not video:
        return base / f"{year_hint:04d}" / "_sans_exif"

    year = f"{dt.year:04d}"
    if video:
        return base / year / "video"

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

    base = organization_base(source, options.output_dir)
    year_hint = _source_year_hint(source)
    media = collect_media(source, include_videos=options.include_videos)

    if progress_cb:
        progress_cb(0, max(len(media), 1), "Recherche des doublons…")

    plan.duplicate_groups = find_duplicates(media, progress_cb=progress_cb)

    # Fichiers à exclure du déplacement principal (doublons non conservés)
    excluded: set[str] = set()
    if options.duplicate_action != DuplicateAction.KEEP_BOTH:
        for group in plan.duplicate_groups:
            for dup in group.duplicates:
                try:
                    excluded.add(str(dup.resolve()).casefold())
                except OSError:
                    excluded.add(str(dup).casefold())

    # 1) Traiter d'abord les doublons (libère les chemins cibles)
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

    vacating = {
        str(m.source.resolve()).casefold()
        for m in plan.moves
        if m.reason in ("doublon", "suppression")
    }

    # 2) Organiser les fichiers conservés
    total = len(media) or 1
    used_names: dict[Path, set[str]] = {}

    for i, path in enumerate(media):
        if progress_cb:
            progress_cb(i + 1, total, f"Analyse : {path.name}")

        try:
            resolved_key = str(path.resolve()).casefold()
        except OSError:
            resolved_key = str(path).casefold()
        if resolved_key in excluded:
            continue

        try:
            capture = get_capture_info(path)
            dt = capture.value
            video = is_video(path)

            # Dossier année (ex. 2005) : ne pas sortir vers une autre année
            # sauf EXIF Original/Digitized fiable.
            force_sans_exif = False
            if year_hint is not None and not video and dt.year != year_hint:
                if capture.source in ("exif_original", "exif_digitized"):
                    force_sans_exif = False  # on fait confiance à l'EXIF
                else:
                    # mtime, DateTime faible, ou nom avec une autre année → rester
                    force_sans_exif = True

            folder = _target_folder(
                dt,
                options,
                base,
                video=video,
                no_exif=force_sans_exif,
                year_hint=year_hint,
            )
            if video:
                reason = "video"
            elif force_sans_exif:
                reason = "sans_exif"
            else:
                reason = "organisation"

            # Renommer seulement avec une date fiable (EXIF prise de vue ou nom)
            if (
                options.rename_with_datetime
                and capture.is_reliable
                and not force_sans_exif
            ):
                new_name = f"{format_datetime_for_filename(dt)}{path.suffix.lower()}"
            else:
                new_name = path.name

            names = used_names.setdefault(folder, set())
            candidate = folder / new_name
            conflict = False
            if new_name in names:
                conflict = True
            elif candidate.exists():
                try:
                    cand_key = str(candidate.resolve()).casefold()
                except OSError:
                    cand_key = str(candidate).casefold()
                if cand_key != resolved_key and cand_key not in vacating:
                    conflict = True

            if conflict:
                stem = Path(new_name).stem
                suffix = Path(new_name).suffix
                n = 1
                while True:
                    alt = f"{stem}_{n}{suffix}"
                    alt_path = folder / alt
                    taken = alt in names
                    if not taken and alt_path.exists():
                        try:
                            alt_key = str(alt_path.resolve()).casefold()
                        except OSError:
                            alt_key = str(alt_path).casefold()
                        if alt_key != resolved_key and alt_key not in vacating:
                            taken = True
                    if not taken:
                        new_name = alt
                        break
                    n += 1
            names.add(new_name)

            dest = folder / new_name
            sync_dt = dt if capture.is_reliable and not force_sans_exif else None

            if _same_file(dest, path):
                plan.skipped.append((path, "Déjà à la bonne place"))
                if options.sync_file_dates and sync_dt is not None:
                    plan.moves.append(
                        PlannedMove(
                            source=path,
                            destination=path,
                            reason="sync_dates",
                            capture_dt=sync_dt,
                        )
                    )
                continue

            plan.moves.append(
                PlannedMove(
                    source=path,
                    destination=dest,
                    reason=reason,
                    capture_dt=sync_dt,
                )
            )
        except Exception as exc:
            plan.errors.append((path, str(exc)))

    # Fichiers parasites macOS (._* , .DS_Store, …)
    if options.clean_junk:
        roots = {source, base}
        if options.output_dir:
            out = options.output_dir.resolve()
            if out.is_dir():
                roots.add(out)
        junk: list[Path] = []
        for root in roots:
            if root.is_dir():
                junk.extend(collect_junk_files(root))
        seen: set[str] = set()
        for j in junk:
            try:
                rj = str(j.resolve()).casefold()
            except OSError:
                rj = str(j).casefold()
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


def execute_plan(
    plan: OrganizerPlan,
    dry_run: bool = True,
    progress_cb=None,
    *,
    sync_file_dates: bool = True,
) -> list[str]:
    """Exécute le plan. Retourne une liste de messages de log."""
    logs: list[str] = []
    total = len(plan.moves) or 1

    for i, move in enumerate(plan.moves):
        if progress_cb:
            progress_cb(i + 1, total, f"{move.source.name}")

        if move.reason == "sync_dates":
            msg = f"DATES EXIF → fichier {move.source}"
            logs.append(msg)
            if not dry_run and move.capture_dt is not None and move.source.is_file():
                try:
                    set_file_datetime(move.source, move.capture_dt)
                except OSError as exc:
                    logs.append(f"  ERREUR dates : {exc}")
            continue

        if move.reason in ("suppression", "junk"):
            label = "PARASITE" if move.reason == "junk" else "SUPPRIMER"
            msg = f"{label} {move.source}"
            logs.append(msg)
            if not dry_run:
                try:
                    if move.source.is_file():
                        move.source.unlink()
                    else:
                        logs.append("  (fichier déjà absent)")
                except OSError as exc:
                    logs.append(f"  ERREUR : {exc}")
            continue

        msg = f"{move.source} → {move.destination}"
        logs.append(msg)
        if dry_run:
            continue

        try:
            if not move.source.is_file():
                logs.append("  ERREUR : source introuvable (déjà déplacé ?)")
                continue
            move.destination.parent.mkdir(parents=True, exist_ok=True)
            # Sur volume insensible à la casse, éviter d'écraser la source
            if _same_file(move.source, move.destination):
                if move.source.name != move.destination.name:
                    tmp = move.source.with_name(f".__pycture_tmp__{move.source.name}")
                    shutil.move(str(move.source), str(tmp))
                    shutil.move(str(tmp), str(move.destination))
                final = move.destination
            else:
                dest = move.destination
                if dest.exists() and not _same_file(move.source, dest):
                    dest = _unique_destination(dest)
                _remove_appledouble_sidecar(move.source)
                shutil.move(str(move.source), str(dest))
                _remove_appledouble_sidecar(dest)
                if dest != move.destination:
                    logs.append(f"  (renommé en {dest.name} pour éviter un conflit)")
                final = dest

            if sync_file_dates and move.capture_dt is not None and final.is_file():
                try:
                    set_file_datetime(final, move.capture_dt)
                    logs.append(
                        f"  dates fichier → {move.capture_dt.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                except OSError as exc:
                    logs.append(f"  ERREUR dates : {exc}")
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
