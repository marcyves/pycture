"""Interface graphique Pycture — nettoyage et organisation de photos."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .folder_cache import clear_folder_cache
from .merge import (
    MergeOptions,
    MergePlan,
    PlannedMerge,
    build_merge_plan,
    execute_merge_plan,
)
from .organizer import (
    DuplicateAction,
    FolderStructure,
    MediaStats,
    OrganizerOptions,
    OrganizerPlan,
    build_plan,
    collect_media,
    execute_plan,
    remove_empty_dirs,
    scan_inventory,
)
from .photoslibrary import export_photos_library, is_photos_library
from .settings import (
    get_last_output_dir,
    get_last_source_dir,
    remember_paths,
)
from .thumbnails import THUMB_SIZE, make_thumbnail


class PyctureApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Pycture — Organisation de photos")
        self.minsize(1000, 720)
        self.geometry("1180x820")

        self._plan: OrganizerPlan | None = None
        self._merge_plan: MergePlan | None = None
        self._busy = False
        self._thumb_photos: list = []  # garder les références PhotoImage
        self._thumb_load_id = 0

        self._build_ui()
        self._sync_event_state()
        self._restore_last_paths()

    # ── Construction UI ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        # Chemins : source + destination sur la même ligne
        paths = ttk.LabelFrame(main, text="Chemins", padding=10)
        paths.pack(fill=tk.X, **pad)
        paths.columnconfigure(1, weight=1)
        paths.columnconfigure(4, weight=1)

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()

        ttk.Label(paths, text="Source :").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(paths, textvariable=self.source_var).grid(
            row=0, column=1, sticky=tk.EW, padx=(6, 4)
        )
        ttk.Button(paths, text="Parcourir…", command=self._browse_source).grid(
            row=0, column=2, padx=(0, 16)
        )

        ttk.Label(paths, text="Destination :").grid(row=0, column=3, sticky=tk.W)
        ttk.Entry(paths, textvariable=self.output_var).grid(
            row=0, column=4, sticky=tk.EW, padx=(6, 4)
        )
        ttk.Button(paths, text="Parcourir…", command=self._browse_output).grid(
            row=0, column=5
        )
        ttk.Button(
            paths,
            text="Photothèque Apple…",
            command=self._import_photos_library,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        ttk.Label(
            paths,
            text="Destination : optionnelle (organisation) — obligatoire pour fusion / Photothèque",
            foreground="#666",
        ).grid(row=1, column=3, columnspan=3, sticky=tk.W, pady=(8, 0))

        # Options sur 2 colonnes
        opts = ttk.LabelFrame(main, text="Options", padding=10)
        opts.pack(fill=tk.X, **pad)
        left = ttk.Frame(opts)
        right = ttk.Frame(opts)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 12))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(left, text="Structure des dossiers :").grid(
            row=0, column=0, sticky=tk.W, pady=3
        )
        self.structure_var = tk.StringVar()
        structure_combo = ttk.Combobox(
            left,
            textvariable=self.structure_var,
            state="readonly",
            width=28,
        )
        structure_combo.grid(row=0, column=1, sticky=tk.W, pady=3, padx=6)
        structure_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_event_state())

        self._structure_labels = {
            FolderStructure.YEAR_MONTH_DAY.value: "année / mois / jour",
            FolderStructure.YEAR_MONTH_EVENT.value: "année / mois / événement",
            FolderStructure.YEAR_EVENT.value: "année / événement",
        }
        structure_combo.configure(values=list(self._structure_labels.values()))
        self.structure_var.set(self._structure_labels[FolderStructure.YEAR_MONTH_DAY.value])
        self._label_to_structure = {v: k for k, v in self._structure_labels.items()}

        ttk.Label(left, text="Nom de l'événement :").grid(
            row=1, column=0, sticky=tk.W, pady=3
        )
        self.event_var = tk.StringVar()
        self.event_entry = ttk.Entry(left, textvariable=self.event_var, width=30)
        self.event_entry.grid(row=1, column=1, sticky=tk.W, pady=3, padx=6)

        ttk.Label(left, text="Doublons :").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.dup_var = tk.StringVar(value="Déplacer vers _doublons")
        self._dup_labels = {
            DuplicateAction.MOVE_TO_DOUBLONS.value: "Déplacer vers _doublons",
            DuplicateAction.DELETE.value: "Supprimer les doublons",
            DuplicateAction.KEEP_BOTH.value: "Conserver tous les fichiers",
        }
        self._label_to_dup = {v: k for k, v in self._dup_labels.items()}
        ttk.Combobox(
            left,
            textvariable=self.dup_var,
            state="readonly",
            width=28,
            values=list(self._dup_labels.values()),
        ).grid(row=2, column=1, sticky=tk.W, pady=3, padx=6)

        self.rename_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            right,
            text="Renommer avec la date/heure (aaaa-mm-jj hh-mm-ss)",
            variable=self.rename_var,
        ).grid(row=0, column=0, sticky=tk.W, pady=2)

        self.videos_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            right,
            text="Inclure les vidéos → année/video",
            variable=self.videos_var,
        ).grid(row=1, column=0, sticky=tk.W, pady=2)

        self.clean_empty_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            right,
            text="Supprimer les dossiers vides après organisation",
            variable=self.clean_empty_var,
        ).grid(row=2, column=0, sticky=tk.W, pady=2)

        self.clean_junk_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            right,
            text="Supprimer les parasites macOS (._* , .DS_Store)",
            variable=self.clean_junk_var,
        ).grid(row=3, column=0, sticky=tk.W, pady=2)

        self.sync_dates_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            right,
            text="Aligner les dates fichier sur l'EXIF (si fiable)",
            variable=self.sync_dates_var,
        ).grid(row=4, column=0, sticky=tk.W, pady=2)

        self.move_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            right,
            text="Déplacer au lieu de copier (fusion)",
            variable=self.move_var,
        ).grid(row=5, column=0, sticky=tk.W, pady=2)

        # Résumé
        summary = ttk.LabelFrame(main, text="Résumé", padding=10)
        summary.pack(fill=tk.X, **pad)
        self.summary_text = tk.Text(
            summary,
            height=8,
            wrap=tk.WORD,
            state=tk.DISABLED,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            font=("Menlo", 11) if self._has_font("Menlo") else ("Courier", 11),
        )
        self.summary_text.pack(fill=tk.X)
        self._set_summary_idle()

        # Actions
        actions = ttk.Frame(main)
        actions.pack(fill=tk.X, **pad)

        self.preview_btn = ttk.Button(
            actions, text="Analyser (aperçu)", command=self._run_preview
        )
        self.preview_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.merge_btn = ttk.Button(
            actions, text="Fusionner…", command=self._run_merge_preview
        )
        self.merge_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.apply_btn = ttk.Button(
            actions, text="Appliquer", command=self._run_apply, state=tk.DISABLED
        )
        self.apply_btn.pack(side=tk.LEFT)

        self.clear_cache_btn = ttk.Button(
            actions, text="Vider le cache…", command=self._clear_caches
        )
        self.clear_cache_btn.pack(side=tk.RIGHT)

        # Progression
        prog = ttk.Frame(main)
        prog.pack(fill=tk.X, **pad)
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.pack(fill=tk.X, side=tk.TOP)
        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(prog, textvariable=self.status_var).pack(anchor=tk.W, pady=(4, 0))

        # Zone basse : journal + miniatures
        paned = ttk.Panedwindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, **pad)

        log_frame = ttk.LabelFrame(paned, text="Journal", padding=8)
        paned.add(log_frame, weight=1)

        self.log = tk.Text(log_frame, height=16, wrap=tk.WORD, state=tk.DISABLED)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        thumb_outer = ttk.LabelFrame(paned, text="Aperçu miniatures", padding=8)
        paned.add(thumb_outer, weight=2)

        self.thumb_canvas = tk.Canvas(thumb_outer, highlightthickness=0)
        thumb_scroll = ttk.Scrollbar(
            thumb_outer, orient=tk.VERTICAL, command=self.thumb_canvas.yview
        )
        self.thumb_inner = ttk.Frame(self.thumb_canvas)
        self.thumb_inner.bind(
            "<Configure>",
            lambda e: self.thumb_canvas.configure(scrollregion=self.thumb_canvas.bbox("all")),
        )
        self._thumb_window = self.thumb_canvas.create_window(
            (0, 0), window=self.thumb_inner, anchor=tk.NW
        )
        self.thumb_canvas.configure(yscrollcommand=thumb_scroll.set)
        self.thumb_canvas.bind("<Configure>", self._on_thumb_canvas_configure)
        self.thumb_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        thumb_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Molette
        self.thumb_canvas.bind(
            "<Enter>",
            lambda _e: self.thumb_canvas.bind_all("<MouseWheel>", self._on_thumb_mousewheel),
        )
        self.thumb_canvas.bind(
            "<Leave>",
            lambda _e: self.thumb_canvas.unbind_all("<MouseWheel>"),
        )

        self.detail_var = tk.StringVar(value="Sélectionnez une miniature pour le détail.")
        ttk.Label(thumb_outer, textvariable=self.detail_var, wraplength=420).pack(
            fill=tk.X, pady=(6, 0)
        )

    def _has_font(self, family: str) -> bool:
        try:
            from tkinter import font as tkfont

            return family in tkfont.families()
        except Exception:
            return False

    def _set_summary_text(self, text: str) -> None:
        self.summary_text.configure(state=tk.NORMAL)
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state=tk.DISABLED)

    def _set_summary_idle(self) -> None:
        self._set_summary_text(
            "Choisissez un dossier source pour l'inventaire,\n"
            "puis Analysez pour les doublons et l'état d'organisation."
        )

    def _format_inventory_summary(self, stats: MediaStats, *, analyzed: bool = False) -> str:
        lines = [
            f"Photos : {stats.photo_total:>5}   ({stats.format_ext_counts(stats.photos_by_ext)})",
            f"Vidéos : {stats.video_total:>5}   ({stats.format_ext_counts(stats.videos_by_ext)})",
            f"Total  : {stats.media_total:>5}",
        ]
        if analyzed:
            lines += [
                "",
                f"Doublons        : {stats.duplicate_groups} groupes "
                f"({stats.duplicate_extras} fichiers en trop)",
                f"Déjà corrects   : {stats.already_correct}",
                f"À organiser     : {stats.to_organize}",
                f"Sans EXIF       : {stats.sans_exif}",
                f"Vidéos à bouger : {stats.videos_to_move}",
                f"Dates à aligner : {stats.sync_dates}",
                f"Parasites       : {stats.junk}",
                f"Erreurs         : {stats.errors}",
            ]
            if stats.cache_hits or stats.cache_misses:
                lines.append(
                    f"Cache           : {stats.cache_hits} hits / {stats.cache_misses} miss"
                )
        else:
            lines += ["", "Analyse non lancée — doublons et « déjà corrects » indisponibles."]
        return "\n".join(lines)

    def _refresh_inventory_async(self, root: Path) -> None:
        include_videos = self.videos_var.get()
        self._set_summary_text("Inventaire en cours…")

        def worker() -> None:
            try:
                stats = scan_inventory(root, include_videos=include_videos)
                text = self._format_inventory_summary(stats, analyzed=False)
            except Exception as exc:
                text = f"Erreur inventaire : {exc}"
            self.after(0, lambda: self._set_summary_text(text))

        threading.Thread(target=worker, daemon=True).start()

    def _update_summary_from_plan(self, plan: OrganizerPlan) -> None:
        self._set_summary_text(self._format_inventory_summary(plan.stats, analyzed=True))

    # ── Helpers UI ───────────────────────────────────────────────────

    def _on_thumb_canvas_configure(self, event) -> None:
        self.thumb_canvas.itemconfigure(self._thumb_window, width=event.width)

    def _on_thumb_mousewheel(self, event) -> None:
        self.thumb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _sync_event_state(self) -> None:
        label = self.structure_var.get()
        key = self._label_to_structure.get(label, FolderStructure.YEAR_MONTH_DAY.value)
        needs_event = key in (
            FolderStructure.YEAR_MONTH_EVENT.value,
            FolderStructure.YEAR_EVENT.value,
        )
        self.event_entry.configure(state=tk.NORMAL if needs_event else tk.DISABLED)

    def _restore_last_paths(self) -> None:
        source = get_last_source_dir()
        if source and Path(source).is_dir():
            self.source_var.set(source)
            self._load_folder_thumbnails(Path(source))
            self._refresh_inventory_async(Path(source))
        output = get_last_output_dir()
        if output and Path(output).is_dir():
            self.output_var.set(output)

    def _browse_source(self) -> None:
        initial = self.source_var.get().strip() or None
        path = filedialog.askdirectory(
            title="Choisir le dossier de photos",
            initialdir=initial if initial and Path(initial).is_dir() else None,
        )
        if path:
            self.source_var.set(path)
            remember_paths(source_dir=path)
            self._load_folder_thumbnails(Path(path))
            self._refresh_inventory_async(Path(path))

    def _browse_output(self) -> None:
        initial = self.output_var.get().strip() or self.source_var.get().strip() or None
        path = filedialog.askdirectory(
            title="Choisir le dossier de destination",
            initialdir=initial if initial and Path(initial).is_dir() else None,
        )
        if path:
            self.output_var.set(path)
            remember_paths(output_dir=path)

    def _clear_caches(self) -> None:
        if self._busy:
            return
        roots: list[Path] = []
        source = self.source_var.get().strip()
        if source and Path(source).is_dir():
            roots.append(Path(source))
        dest = self.output_var.get().strip()
        if dest and Path(dest).is_dir():
            dest_path = Path(dest)
            if not any(r.resolve() == dest_path.resolve() for r in roots):
                roots.append(dest_path)
        if not roots:
            messagebox.showwarning(
                "Aucun dossier",
                "Indiquez un dossier source (et éventuellement une destination).",
            )
            return
        labels = "\n".join(f"• {r}/.pycture/cache.sqlite" for r in roots)
        if not messagebox.askyesno(
            "Vider le cache",
            f"Supprimer le cache Pycture de :\n\n{labels}\n\nContinuer ?",
        ):
            return
        cleared = 0
        for root in roots:
            if clear_folder_cache(root):
                cleared += 1
        if cleared:
            messagebox.showinfo(
                "Cache vidé",
                f"Cache effacé pour {cleared} dossier(s).",
            )
        else:
            messagebox.showinfo("Cache", "Aucun fichier de cache trouvé.")

    def _import_photos_library(self) -> None:
        if self._busy:
            return

        # Destination obligatoire en mode photothèque
        dest = self.output_var.get().strip()
        if not dest:
            messagebox.showwarning(
                "Destination obligatoire",
                "En mode Photothèque Apple, choisissez d'abord un dossier de destination\n"
                "(où copier les originaux).",
            )
            self._browse_output()
            dest = self.output_var.get().strip()
            if not dest:
                return

        dest_path = Path(dest)
        if not dest_path.exists():
            try:
                dest_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror(
                    "Destination invalide",
                    f"Impossible de créer le dossier de destination :\n{exc}",
                )
                return
        if not dest_path.is_dir():
            messagebox.showerror(
                "Destination invalide",
                f"Ce chemin n'est pas un dossier :\n{dest_path}",
            )
            return

        # Sur macOS, .photoslibrary est un package (dossier)
        library = filedialog.askdirectory(
            title="Choisir une photothèque Apple (.photoslibrary)",
        )
        if not library:
            return
        lib_path = Path(library)
        if not is_photos_library(lib_path):
            messagebox.showerror(
                "Photothèque invalide",
                "Sélectionnez un paquet « Nom.photoslibrary »\n"
                "(dossier dont le nom se termine par .photoslibrary).",
            )
            return

        try:
            if dest_path.resolve() == lib_path.resolve() or lib_path.resolve() in dest_path.resolve().parents:
                messagebox.showerror(
                    "Destination invalide",
                    "La destination ne peut pas être la photothèque elle-même\n"
                    "ni un dossier à l'intérieur.",
                )
                return
        except OSError:
            pass

        if not messagebox.askyesno(
            "Exporter la photothèque",
            f"Copier les originaux de :\n{lib_path}\n\nvers :\n{dest_path}\n\n"
            "La photothèque ne sera pas modifiée.\n"
            "Les fichiers uniquement dans iCloud (non téléchargés) seront ignorés.\n\n"
            "Continuer ?",
        ):
            return

        self._set_busy(True)
        self._append_log(f"\n=== Export photothèque Apple ===\n{lib_path}\n→ {dest_path}\n")
        include_videos = self.videos_var.get()

        def worker() -> None:
            try:
                result = export_photos_library(
                    lib_path,
                    dest_path,
                    include_videos=include_videos,
                    progress_cb=self._progress_cb,
                )
                self.after(0, lambda: self._on_photos_library_done(result))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_photos_library_done(self, result) -> None:
        self._append_log(result.summary)
        if result.errors:
            self._append_log("--- Erreurs export ---")
            for path, err in result.errors[:50]:
                self._append_log(f"  {path.name} : {err}")
        if result.skipped_missing:
            self._append_log(
                f"({len(result.skipped_missing)} fichier(s) absents — "
                "téléchargez-les dans Photos si besoin)"
            )

        self.progress["value"] = 0
        self._set_busy(False)

        if result.copied:
            self.source_var.set(str(result.destination))
            remember_paths(source_dir=str(result.destination))
            self._load_folder_thumbnails(result.destination)
            self._refresh_inventory_async(result.destination)
            self.status_var.set(
                f"Export terminé : {len(result.copied)} fichier(s). "
                "Source mise à jour — vous pouvez Analyser."
            )
            messagebox.showinfo(
                "Export terminé",
                f"{len(result.copied)} fichier(s) copiés vers :\n{result.destination}\n\n"
                "Le dossier source a été mis à jour. Cliquez sur Analyser.",
            )
        else:
            self.status_var.set("Export : aucun fichier copié.")
            messagebox.showwarning(
                "Aucun fichier",
                "Aucun original n'a été copié.\n"
                "Vérifiez les permissions (Confidentialité > Fichiers et dossiers / Accès disque complet)\n"
                "et que les photos sont téléchargées localement (pas seulement iCloud).",
            )

    def _append_log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def _clear_thumbnails(self) -> None:
        for child in self.thumb_inner.winfo_children():
            child.destroy()
        self._thumb_photos.clear()
        self.detail_var.set("Sélectionnez une miniature pour le détail.")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.preview_btn.configure(state=state)
        self.merge_btn.configure(state=state)
        self.clear_cache_btn.configure(state=state)
        if busy:
            self.apply_btn.configure(state=tk.DISABLED)
        elif self._merge_plan and self._merge_plan.to_merge:
            self.apply_btn.configure(state=tk.NORMAL)
        elif self._plan and self._plan.moves:
            self.apply_btn.configure(state=tk.NORMAL)
        else:
            self.apply_btn.configure(state=tk.DISABLED)

    def _progress_cb(self, current: int, total: int, message: str) -> None:
        def update() -> None:
            self.progress["maximum"] = max(total, 1)
            self.progress["value"] = current
            self.status_var.set(message)

        self.after(0, update)

    def _options_from_ui(self) -> OrganizerOptions | None:
        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("Dossier manquant", "Choisissez un dossier de travail.")
            return None
        source_path = Path(source)
        if not source_path.is_dir():
            messagebox.showerror("Erreur", f"Dossier introuvable :\n{source}")
            return None

        struct_label = self.structure_var.get()
        structure = FolderStructure(
            self._label_to_structure.get(struct_label, FolderStructure.YEAR_MONTH_DAY.value)
        )
        event = self.event_var.get().strip()
        if structure in (FolderStructure.YEAR_MONTH_EVENT, FolderStructure.YEAR_EVENT):
            if not event:
                messagebox.showwarning(
                    "Événement requis",
                    "Indiquez un nom d'événement pour cette structure de dossiers.",
                )
                return None

        dup_label = self.dup_var.get()
        dup_action = DuplicateAction(
            self._label_to_dup.get(dup_label, DuplicateAction.MOVE_TO_DOUBLONS.value)
        )

        out = self.output_var.get().strip()
        output_dir = Path(out) if out else None

        return OrganizerOptions(
            source_dir=source_path,
            structure=structure,
            event_name=event,
            rename_with_datetime=self.rename_var.get(),
            duplicate_action=dup_action,
            dry_run=True,
            output_dir=output_dir,
            clean_junk=self.clean_junk_var.get(),
            include_videos=self.videos_var.get(),
            sync_file_dates=self.sync_dates_var.get(),
        )

    def _merge_options_from_ui(self) -> MergeOptions | None:
        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("Dossier manquant", "Choisissez un dossier source.")
            return None
        source_path = Path(source)
        if not source_path.is_dir():
            messagebox.showerror("Erreur", f"Dossier source introuvable :\n{source}")
            return None

        dest = self.output_var.get().strip()
        if not dest:
            messagebox.showwarning(
                "Destination obligatoire",
                "Pour fusionner, choisissez un dossier de destination.",
            )
            self._browse_output()
            dest = self.output_var.get().strip()
            if not dest:
                return None

        dest_path = Path(dest)
        if not dest_path.is_dir():
            messagebox.showerror(
                "Destination invalide",
                f"Dossier de destination introuvable :\n{dest}",
            )
            return None

        try:
            if source_path.resolve() == dest_path.resolve():
                messagebox.showerror(
                    "Chemins identiques",
                    "Source et destination doivent être des dossiers différents.",
                )
                return None
        except OSError:
            pass

        return MergeOptions(
            source_dir=source_path,
            destination_dir=dest_path,
            move=self.move_var.get(),
            include_videos=self.videos_var.get(),
        )

    def _remember_current_paths(self) -> None:
        source = self.source_var.get().strip()
        output = self.output_var.get().strip()
        remember_paths(
            source_dir=source or None,
            output_dir=output if output else "",
        )

    # ── Miniatures ───────────────────────────────────────────────────

    def _load_folder_thumbnails(self, root: Path, limit: int = 80) -> None:
        """Aperçu rapide après sélection du dossier (avant analyse)."""
        self._thumb_load_id += 1
        load_id = self._thumb_load_id
        self._clear_thumbnails()
        self.status_var.set("Chargement des miniatures…")

        def worker() -> None:
            try:
                images = collect_media(root, include_videos=True)[:limit]
            except Exception:
                images = []
            items = [(p, p.name, "") for p in images]
            self.after(0, lambda: self._populate_thumbnails(load_id, items, "Dossier source"))

        threading.Thread(target=worker, daemon=True).start()

    def _load_plan_thumbnails(self, plan: OrganizerPlan) -> None:
        """Miniatures basées sur le plan d'analyse (avec destination)."""
        self._thumb_load_id += 1
        load_id = self._thumb_load_id
        self._clear_thumbnails()

        items: list[tuple[Path, str, str]] = []
        for move in plan.moves:
            if move.reason == "junk":
                continue  # listés dans le journal seulement
            if move.reason == "sync_dates":
                continue
            if move.reason == "suppression":
                caption = move.source.name
                badge = "suppression"
            elif move.reason == "sans_exif":
                caption = f"{move.source.name} → {move.destination}"
                badge = "sans EXIF"
            elif move.reason == "video":
                caption = f"{move.source.name} → {move.destination}"
                badge = "vidéo"
            elif move.reason == "doublon":
                caption = f"{move.source.name} → {move.destination.name}"
                badge = "doublon"
            else:
                caption = f"{move.source.name} → {move.destination.name}"
                badge = ""
            items.append((move.source, caption, badge))

        # Ajouter aussi les fichiers déjà bien placés (échantillon)
        for path, _reason in plan.skipped[:40]:
            items.append((path, f"{path.name} (déjà placé)", ""))

        def worker() -> None:
            # Les miniatures sont créées sur le thread UI via after, par lots
            self.after(0, lambda: self._populate_thumbnails(load_id, items, "Plan d'organisation"))

        threading.Thread(target=worker, daemon=True).start()

    def _load_merge_plan_thumbnails(self, plan: MergePlan) -> None:
        """Miniatures basées sur le plan de fusion."""
        self._thumb_load_id += 1
        load_id = self._thumb_load_id
        self._clear_thumbnails()

        items: list[tuple[Path, str, str]] = []
        for action in plan.actions:
            if action.reason == "skip_duplicate":
                caption = f"{action.source.name} (doublon)"
                badge = "doublon"
            elif action.reason == "rename_conflict":
                caption = f"{action.source.name} → {action.destination.name}"
                badge = "renommer"
            else:
                caption = f"{action.source.name} → {action.destination}"
                badge = "déplacer" if plan.move else "copier"
            items.append((action.source, caption, badge))

        self.after(
            0,
            lambda: self._populate_thumbnails(load_id, items, "Plan de fusion"),
        )

    def _populate_thumbnails(
        self,
        load_id: int,
        items: list[tuple[Path, str, str]],
        title: str,
    ) -> None:
        if load_id != self._thumb_load_id:
            return

        self._clear_thumbnails()
        if not items:
            self.detail_var.set("Aucune image à afficher.")
            self.status_var.set("Aucune miniature.")
            return

        cols = max(3, (self.thumb_canvas.winfo_width() or 480) // (THUMB_SIZE[0] + 24))
        batch_size = 12
        total = len(items)

        def add_batch(start: int) -> None:
            if load_id != self._thumb_load_id:
                return
            end = min(start + batch_size, total)
            for i in range(start, end):
                path, caption, badge = items[i]
                photo = make_thumbnail(path)
                row, col = divmod(i, cols)

                cell = ttk.Frame(self.thumb_inner, padding=4)
                cell.grid(row=row, column=col, sticky=tk.N)

                if photo is not None:
                    self._thumb_photos.append(photo)
                    btn = tk.Label(cell, image=photo, cursor="hand2", bd=1, relief=tk.SOLID)
                else:
                    btn = tk.Label(
                        cell,
                        text="?",
                        width=12,
                        height=6,
                        cursor="hand2",
                        bd=1,
                        relief=tk.SOLID,
                        bg="#555",
                        fg="white",
                    )
                btn.pack()
                btn.bind(
                    "<Button-1>",
                    lambda _e, p=path, c=caption, b=badge: self._on_thumb_click(p, c, b),
                )

                label_text = path.name if len(path.name) <= 22 else path.name[:19] + "…"
                if badge:
                    label_text = f"[{badge}] {label_text}"
                ttk.Label(cell, text=label_text, wraplength=THUMB_SIZE[0]).pack()

            self.progress["maximum"] = total
            self.progress["value"] = end
            self.status_var.set(f"Miniatures {end}/{total} ({title})")

            if end < total:
                self.after(1, lambda: add_batch(end))
            else:
                self.progress["value"] = 0
                self.status_var.set(f"{total} miniature(s) — {title}")
                self.detail_var.set("Cliquez une miniature pour voir le détail.")

        add_batch(0)

    def _on_thumb_click(self, path: Path, caption: str, badge: str) -> None:
        parts = [str(path), caption]
        if badge:
            parts.append(f"Statut : {badge}")
        # Chercher le move correspondant dans le plan
        if self._merge_plan:
            for action in self._merge_plan.actions:
                try:
                    same = action.source.resolve() == path.resolve()
                except OSError:
                    same = action.source == path
                if not same:
                    continue
                if action.reason == "skip_duplicate":
                    parts.append(f"Ignoré (doublon contenu) ≡ {action.destination}")
                elif action.reason == "rename_conflict":
                    parts.append(f"Renommer → {action.destination}")
                else:
                    verb = "Déplacer" if self._merge_plan.move else "Copier"
                    parts.append(f"{verb} → {action.destination}")
                break
        elif self._plan:
            for move in self._plan.moves:
                if move.source.resolve() == path.resolve():
                    if move.reason == "suppression":
                        parts.append("Action : suppression (doublon)")
                    elif move.reason == "junk":
                        parts.append("Action : suppression (fichier parasite macOS)")
                    elif move.reason == "sans_exif":
                        parts.append(f"Sans EXIF → {move.destination}")
                    elif move.reason == "video":
                        parts.append(f"Destination vidéo : {move.destination}")
                    else:
                        parts.append(f"Destination : {move.destination}")
                    break
        self.detail_var.set("\n".join(parts))

    # ── Actions ──────────────────────────────────────────────────────

    def _run_preview(self) -> None:
        if self._busy:
            return
        options = self._options_from_ui()
        if not options:
            return

        self._remember_current_paths()
        self._clear_log()
        self._append_log("Analyse en cours…")
        self._set_busy(True)
        self._plan = None
        self._merge_plan = None

        def worker() -> None:
            try:
                plan = build_plan(options, progress_cb=self._progress_cb)
                self.after(0, lambda: self._on_preview_done(plan, options))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_preview_done(self, plan: OrganizerPlan, options: OrganizerOptions) -> None:
        self._plan = plan
        self._update_summary_from_plan(plan)
        self._clear_log()
        self._append_log("=== Aperçu (aucune modification) ===\n")
        self._append_log(plan.summary)
        self._append_log("")

        if plan.duplicate_groups:
            self._append_log("--- Doublons détectés ---")
            # Index destination finale du fichier conservé (s'il est déplacé)
            final_by_source = {
                str(m.source.resolve()).casefold(): m.destination
                for m in plan.moves
                if m.reason in ("organisation", "video")
            }
            for g in plan.duplicate_groups:
                keep = g.keeper
                if keep is None:
                    continue
                try:
                    keep_key = str(keep.resolve()).casefold()
                except OSError:
                    keep_key = str(keep).casefold()
                final = final_by_source.get(keep_key)
                if final and str(final) != str(keep):
                    self._append_log(f"  Conservé : {keep}")
                    self._append_log(f"           → sera déplacé vers : {final}")
                else:
                    self._append_log(f"  Conservé : {keep}")
                for d in g.duplicates:
                    self._append_log(f"  Doublon  : {d}")
            self._append_log("")

        self._append_log("--- Actions prévues ---")
        for move in plan.moves[:200]:
            if move.reason == "suppression":
                self._append_log(f"SUPPRIMER  {move.source}")
            elif move.reason == "junk":
                self._append_log(f"PARASITE   {move.source}")
            elif move.reason == "sync_dates":
                when = (
                    move.capture_dt.strftime("%Y-%m-%d %H:%M:%S")
                    if move.capture_dt
                    else "?"
                )
                self._append_log(f"DATES      {move.source.name} → {when}")
            elif move.reason == "sans_exif":
                self._append_log(f"[sans EXIF] {move.source.name} → {move.destination}")
            elif move.reason == "video":
                self._append_log(f"[vidéo] {move.source.name} → {move.destination}")
            else:
                tag = "[doublon] " if move.reason == "doublon" else ""
                self._append_log(f"{tag}{move.source.name} → {move.destination}")
        if len(plan.moves) > 200:
            self._append_log(f"… et {len(plan.moves) - 200} autres actions")

        if plan.skipped:
            self._append_log(f"\n({len(plan.skipped)} fichier(s) déjà bien placés)")
        if plan.errors:
            self._append_log("\n--- Erreurs ---")
            for path, err in plan.errors:
                self._append_log(f"  {path} : {err}")

        structure = options.structure.value
        self._append_log(f"\nStructure : {self._structure_labels.get(structure, structure)}")
        if options.rename_with_datetime:
            self._append_log("Renommage : aaaa-mm-jj hh-mm-ss")
        else:
            self._append_log("Renommage : noms conservés")

        self._load_plan_thumbnails(plan)

        self.status_var.set("Aperçu terminé. Vérifiez les miniatures puis cliquez sur Appliquer.")
        self.progress["value"] = 0
        self._set_busy(False)

    def _run_merge_preview(self) -> None:
        if self._busy:
            return
        options = self._merge_options_from_ui()
        if not options:
            return

        self._remember_current_paths()
        self._clear_log()
        self._append_log("Analyse fusion en cours…")
        self._set_busy(True)
        self._plan = None
        self._merge_plan = None

        def worker() -> None:
            try:
                plan = build_merge_plan(options, progress_cb=self._progress_cb)
                self.after(0, lambda: self._on_merge_preview_done(plan, options))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _format_merge_action_log(self, action: PlannedMerge, *, move: bool) -> str:
        if action.reason == "skip_duplicate":
            return f"IGNORER (doublon) {action.source} ≡ {action.destination}"
        if action.reason == "rename_conflict":
            return f"RENOMMER {action.source} → {action.destination}"
        verb = "DÉPLACER" if move else "COPIER"
        return f"{verb} {action.source} → {action.destination}"

    def _on_merge_preview_done(self, plan: MergePlan, options: MergeOptions) -> None:
        self._merge_plan = plan
        self._plan = None
        self._set_summary_text(plan.summary)
        self._clear_log()
        self._append_log("=== Aperçu fusion (aucune modification) ===\n")
        self._append_log(plan.summary)
        self._append_log("")
        self._append_log(f"Source      : {options.source_dir}")
        self._append_log(f"Destination : {options.destination_dir}")
        self._append_log("")

        self._append_log("--- Actions prévues ---")
        for action in plan.actions[:200]:
            self._append_log(self._format_merge_action_log(action, move=plan.move))
        if len(plan.actions) > 200:
            self._append_log(f"… et {len(plan.actions) - 200} autres actions")

        if plan.errors:
            self._append_log("\n--- Erreurs ---")
            for path, err in plan.errors:
                self._append_log(f"  {path} : {err}")

        self._load_merge_plan_thumbnails(plan)

        if plan.to_merge:
            self.status_var.set(
                "Aperçu fusion terminé. Vérifiez puis cliquez sur Appliquer."
            )
        else:
            self.status_var.set("Aperçu fusion terminé — rien à fusionner.")
        self.progress["value"] = 0
        self._set_busy(False)

    def _run_apply(self) -> None:
        if self._busy:
            return
        if self._merge_plan is not None:
            self._run_apply_merge()
            return
        if not self._plan:
            return

        n = len(self._plan.moves)
        if n == 0:
            messagebox.showinfo("Rien à faire", "Aucune action à appliquer.")
            return

        dup_deletes = sum(1 for m in self._plan.moves if m.reason == "suppression")
        warning = f"{n} action(s) vont être appliquées."
        if dup_deletes:
            warning += f"\nDont {dup_deletes} suppression(s) de doublons (irréversible)."
        warning += "\n\nContinuer ?"

        if not messagebox.askyesno("Confirmer", warning):
            return

        options = self._options_from_ui()
        if not options:
            return

        self._set_busy(True)
        self._append_log("\n=== Application ===\n")
        plan = self._plan

        def worker() -> None:
            try:
                logs = execute_plan(
                    plan,
                    dry_run=False,
                    progress_cb=self._progress_cb,
                    sync_file_dates=options.sync_file_dates,
                )
                removed: list[Path] = []
                if self.clean_empty_var.get():
                    root = (options.output_dir or options.source_dir).resolve()
                    removed = remove_empty_dirs(root, dry_run=False)
                self.after(0, lambda: self._on_apply_done(logs, removed))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _run_apply_merge(self) -> None:
        if self._busy or not self._merge_plan:
            return

        n = len(self._merge_plan.to_merge)
        if n == 0:
            messagebox.showinfo("Rien à faire", "Aucun fichier à fusionner.")
            return

        verb = "déplacés" if self._merge_plan.move else "copiés"
        warning = (
            f"{n} fichier(s) vont être {verb} vers la destination.\n"
            f"Ignorés (doublon) : {len(self._merge_plan.skipped)}\n"
            f"Renommages conflit : {len(self._merge_plan.renames)}\n\n"
            "Continuer ?"
        )
        if not messagebox.askyesno("Confirmer la fusion", warning):
            return

        self._set_busy(True)
        self._append_log("\n=== Application fusion ===\n")
        plan = self._merge_plan

        def worker() -> None:
            try:
                logs = execute_merge_plan(
                    plan,
                    dry_run=False,
                    progress_cb=self._progress_cb,
                )
                self.after(0, lambda: self._on_merge_apply_done(logs, plan))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_merge_apply_done(self, logs: list[str], plan: MergePlan) -> None:
        for line in logs:
            self._append_log(line)

        errors = sum(1 for line in logs if "ERREUR" in line)
        self._append_log("\n--- Résumé fusion ---")
        self._append_log(f"Fusionnés : {len(plan.to_merge)}")
        self._append_log(f"  dont renommages conflit : {len(plan.renames)}")
        self._append_log(f"Ignorés (doublon contenu) : {len(plan.skipped)}")
        self._append_log(f"Erreurs : {errors}")
        self._append_log("\nTerminé.")

        dest = self.output_var.get().strip()
        if dest and Path(dest).is_dir():
            self._refresh_inventory_async(Path(dest))

        self.status_var.set("Fusion terminée.")
        self.progress["value"] = 0
        self._merge_plan = None
        self._set_busy(False)
        messagebox.showinfo("Terminé", "La fusion des dossiers est terminée.")

    def _on_apply_done(self, logs: list[str], removed: list[Path]) -> None:
        for line in logs:
            self._append_log(line)
        if removed:
            self._append_log(f"\nDossiers vides supprimés : {len(removed)}")
            for d in removed[:50]:
                self._append_log(f"  {d}")
            if len(removed) > 50:
                self._append_log(f"  … et {len(removed) - 50} autres")

        self._append_log("\nTerminé.")
        self.status_var.set("Organisation terminée.")
        self.progress["value"] = 0
        self._plan = None
        self._merge_plan = None
        self._set_busy(False)
        messagebox.showinfo("Terminé", "L'organisation des photos est terminée.")

    def _on_error(self, exc: Exception) -> None:
        self._append_log(f"\nERREUR : {exc}")
        self.status_var.set("Erreur.")
        self.progress["value"] = 0
        self._set_busy(False)
        messagebox.showerror("Erreur", str(exc))


def run_app() -> None:
    app = PyctureApp()
    app.mainloop()
