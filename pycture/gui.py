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
from .photoslibrary import (
    export_photos_library,
    is_apple_media_library,
    library_kind,
)
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
        self.minsize(1050, 750)
        self.geometry("1200x850")

        self._plan: OrganizerPlan | None = None
        self._merge_plan: MergePlan | None = None
        self._busy = False
        self._thumb_photos: list = []  # Garder les références PhotoImage
        self._thumb_load_id = 0
        self._job_phases: list[tuple[str, str]] = []  # (préfixe message, libellé)
        self._job_phase_index: int = -1
        self._progress_log_milestone: int = -1

        # Configuration des styles de l'interface
        self._setup_styles()

        self._build_ui()
        self._sync_event_state()
        self._restore_last_paths()

    def _setup_styles(self) -> None:
        """Configure les polices et l'aspect visuel des widgets ttk."""
        self.style = ttk.Style(self)

        # Choix d'un thème de base propre selon l'OS
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        # Couleurs de base modernes
        bg_color = "#f8f9fa"
        accent_color = "#007acc"
        text_muted = "#555555"

        self.configure(bg=bg_color)
        self.style.configure(".", background=bg_color, font=("Segoe UI", 10))

        # Personnalisation des cadres (LabelFrames)
        self.style.configure(
            "TLabelframe",
            background=bg_color,
            borderwidth=1,
            relief="solid",
        )
        self.style.configure(
            "TLabelframe.Label",
            font=("Segoe UI", 10, "bold"),
            foreground=accent_color,
            background=bg_color,
        )

        # Style des boutons
        self.style.configure(
            "Accent.TButton",
            font=("Segoe UI", 10, "bold"),
            foreground="white",
            background=accent_color,
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", "#0062a3"), ("disabled", "#cccccc")],
        )

        # Barre d'état et messages discrets
        self.style.configure("Muted.TLabel", foreground=text_muted, font=("Segoe UI", 9, "italic"))

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        # ── SECTION 1 : CHEMINS ───────────────────────────────────────────
        paths = ttk.LabelFrame(main, text=" 📂 Configuration des Chemins ", padding=14)
        paths.pack(fill=tk.X, pady=(0, 10))
        paths.columnconfigure(1, weight=1)
        paths.columnconfigure(4, weight=1)

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()

        # Source
        ttk.Label(paths, text="Dossier Source :").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(paths, textvariable=self.source_var).grid(
            row=0, column=1, sticky=tk.EW, padx=(6, 6), pady=4
        )
        ttk.Button(paths, text="Parcourir…", command=self._browse_source).grid(
            row=0, column=2, padx=(0, 20), pady=4
        )
        self.source_thumbs_btn = ttk.Button(
            paths,
            text="Générer miniatures",
            command=lambda: self._generate_folder_thumbnails("source"),
        )
        self.source_thumbs_btn.grid(row=1, column=1, sticky=tk.W, padx=(6, 6), pady=(0, 4))

        # Destination
        ttk.Label(paths, text="Destination :").grid(row=0, column=3, sticky=tk.W, pady=4)
        ttk.Entry(paths, textvariable=self.output_var).grid(
            row=0, column=4, sticky=tk.EW, padx=(6, 6), pady=4
        )
        ttk.Button(paths, text="Parcourir…", command=self._browse_output).grid(
            row=0, column=5, pady=4
        )
        self.dest_thumbs_btn = ttk.Button(
            paths,
            text="Générer miniatures",
            command=lambda: self._generate_folder_thumbnails("destination"),
        )
        self.dest_thumbs_btn.grid(row=1, column=4, sticky=tk.W, padx=(6, 6), pady=(0, 4))

        # Actions avancées / Aide contextuelle sur la ligne du bas
        tools_frame = ttk.Frame(paths)
        tools_frame.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(10, 0))

        ttk.Button(
            tools_frame,
            text="✨ Importer une Photothèque Apple…",
            command=self._import_photos_library,
        ).pack(side=tk.LEFT)

        ttk.Label(
            tools_frame,
            text="💡 Note : La destination est obligatoire pour les fusions et l'import Apple.",
            style="Muted.TLabel",
        ).pack(side=tk.RIGHT, padx=6)

        # ── SECTION 2 : OPTIONS & RÉSUMÉ ─────────────────────────────────
        middle_zone = ttk.Frame(main)
        middle_zone.pack(fill=tk.X, pady=10)

        # Colonne de gauche : Formulaire des options
        opts = ttk.LabelFrame(middle_zone, text=" ⚙️ Paramètres d'organisation ", padding=14)
        opts.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        # Grille pour organiser proprement les options
        opts.columnconfigure(1, weight=1)

        ttk.Label(opts, text="Structure de dossiers :").grid(row=0, column=0, sticky=tk.W, pady=6)
        self.structure_var = tk.StringVar()
        structure_combo = ttk.Combobox(
            opts, textvariable=self.structure_var, state="readonly"
        )
        structure_combo.grid(row=0, column=1, sticky=tk.EW, pady=6, padx=(6, 0))
        structure_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_event_state())

        self._structure_labels = {
            FolderStructure.YEAR_MONTH_DAY.value: "année / mois / jour",
            FolderStructure.YEAR_MONTH_EVENT.value: "année / mois / événement",
            FolderStructure.YEAR_EVENT.value: "année / événement",
        }
        structure_combo.configure(values=list(self._structure_labels.values()))
        self.structure_var.set(self._structure_labels[FolderStructure.YEAR_MONTH_DAY.value])
        self._label_to_structure = {v: k for k, v in self._structure_labels.items()}

        ttk.Label(opts, text="Nom de l'événement :").grid(row=1, column=0, sticky=tk.W, pady=6)
        self.event_var = tk.StringVar()
        self.event_entry = ttk.Entry(opts, textvariable=self.event_var)
        self.event_entry.grid(row=1, column=1, sticky=tk.EW, pady=6, padx=(6, 0))

        ttk.Label(opts, text="Gestion des doublons :").grid(row=2, column=0, sticky=tk.W, pady=6)
        self.dup_var = tk.StringVar(value="Déplacer vers _doublons")
        self._dup_labels = {
            DuplicateAction.MOVE_TO_DOUBLONS.value: "Déplacer vers _doublons",
            DuplicateAction.DELETE.value: "Supprimer les doublons",
            DuplicateAction.KEEP_BOTH.value: "Conserver tous les fichiers",
        }
        self._label_to_dup = {v: k for k, v in self._dup_labels.items()}
        ttk.Combobox(
            opts,
            textvariable=self.dup_var,
            state="readonly",
            values=list(self._dup_labels.values()),
        ).grid(row=2, column=1, sticky=tk.EW, pady=6, padx=(6, 0))

        # Colonne de droite : Checkboxes & Résumé d'inventaire
        right_panel = ttk.Frame(middle_zone)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        chk_frame = ttk.Frame(right_panel)
        chk_frame.pack(fill=tk.X, pady=(0, 6))

        # Rangement des cases à cocher en sous-grille (2 colonnes) pour compacter
        self.rename_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            chk_frame, text="Renommer via EXIF (date/heure)", variable=self.rename_var
        ).grid(row=0, column=0, sticky=tk.W, pady=2, padx=(0, 10))

        self.videos_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            chk_frame, text="Inclure les vidéos", variable=self.videos_var
        ).grid(row=0, column=1, sticky=tk.W, pady=2)

        self.clean_empty_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            chk_frame, text="Nettoyer les dossiers vides", variable=self.clean_empty_var
        ).grid(row=1, column=0, sticky=tk.W, pady=2, padx=(0, 10))

        self.clean_junk_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            chk_frame, text="Supprimer fichiers parasites macOS", variable=self.clean_junk_var
        ).grid(row=1, column=1, sticky=tk.W, pady=2)

        self.sync_dates_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            chk_frame, text="Aligner dates fichier sur EXIF", variable=self.sync_dates_var
        ).grid(row=2, column=0, sticky=tk.W, pady=2, padx=(0, 10))

        # Résumé d'inventaire
        summary = ttk.LabelFrame(right_panel, text=" Résumé de l'analyse ", padding=10)
        summary.pack(fill=tk.BOTH, expand=True)
        self.summary_text = tk.Text(
            summary,
            height=6,
            wrap=tk.WORD,
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=0,
            background="#ffffff",
            foreground="#1a1a1a",
            insertbackground="#1a1a1a",
            font=("Consolas", 10) if self._has_font("Consolas") else ("Courier", 10),
        )
        self.summary_text.bind("<Key>", lambda _e: "break")
        self.summary_text.bind("<<Paste>>", lambda _e: "break")
        self.summary_text.pack(fill=tk.BOTH, expand=True)
        self._set_summary_idle()

        # ── SECTION 3 : ACTIONS PRINCIPALES ───────────────────────────────
        actions_bar = ttk.Frame(main, padding=(0, 10))
        actions_bar.pack(fill=tk.X)

        # Bloc : Organisation standard
        org_group = ttk.LabelFrame(actions_bar, text=" Mode Organisation ", padding=6)
        org_group.pack(side=tk.LEFT, padx=(0, 12))
        self.preview_btn = ttk.Button(org_group, text="🔍 Analyser", command=self._run_preview)
        self.preview_btn.pack(side=tk.LEFT, padx=4, pady=2)
        self.apply_btn = ttk.Button(
            org_group,
            text="🚀 Appliquer l'organisation",
            command=self._run_apply,
            state=tk.DISABLED,
            style="Accent.TButton",
        )
        self.apply_btn.pack(side=tk.LEFT, padx=4, pady=2)

        # Bloc : Fusion (Séparation visuelle nette)
        merge_group = ttk.LabelFrame(actions_bar, text=" Mode Fusion ", padding=6)
        merge_group.pack(side=tk.LEFT)
        self.merge_preview_btn = ttk.Button(
            merge_group, text="🔎 Analyser fusion", command=self._run_merge_preview
        )
        self.merge_preview_btn.pack(side=tk.LEFT, padx=4, pady=2)
        self.merge_apply_btn = ttk.Button(
            merge_group,
            text="🤝 Fusionner les dossiers",
            command=self._run_apply_merge,
            state=tk.DISABLED,
            style="Accent.TButton",
        )
        self.merge_apply_btn.pack(side=tk.LEFT, padx=4, pady=2)
        self.move_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            merge_group,
            text="Déplacer au lieu de copier",
            variable=self.move_var,
        ).pack(side=tk.LEFT, padx=(12, 4), pady=2)

        # Bouton utilitaire à droite
        self.clear_cache_btn = ttk.Button(
            actions_bar, text="🧹 Vider le cache…", command=self._clear_caches
        )
        self.clear_cache_btn.pack(side=tk.RIGHT, pady=6)

        # ── SECTION 4 : PROGRESSION ───────────────────────────────────────
        prog_frame = ttk.Frame(main)
        prog_frame.pack(fill=tk.X, pady=(4, 10))
        self.phase_var = tk.StringVar(value="")
        ttk.Label(prog_frame, textvariable=self.phase_var).pack(anchor=tk.W)
        self.progress = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress.pack(fill=tk.X, side=tk.TOP, pady=(2, 0))
        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(prog_frame, textvariable=self.status_var, style="Muted.TLabel").pack(
            anchor=tk.W, pady=(4, 0)
        )

        # ── SECTION 5 : VISIONNEUSE & JOURNALISME (Zone Basse) ────────────
        paned = ttk.Panedwindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Journal de log (Gauche)
        log_frame = ttk.LabelFrame(paned, text=" Journal d'activité ", padding=8)
        paned.add(log_frame, weight=1)

        self.log = tk.Text(
            log_frame,
            height=12,
            wrap=tk.WORD,
            background="#ffffff",
            foreground="#1a1a1a",
            insertbackground="#1a1a1a",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=0,
            font=("Menlo", 11) if self._has_font("Menlo") else ("Courier", 11),
        )
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        # Lecture seule sans state=DISABLED (sinon texte invisible sur macOS/Tk)
        self.log.bind("<Key>", lambda _e: "break")
        self.log.bind("<<Paste>>", lambda _e: "break")
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Galerie d'aperçus (Droite)
        thumb_outer = ttk.LabelFrame(paned, text=" Aperçu des traitements ", padding=8)
        paned.add(thumb_outer, weight=2)
        try:
            paned.pane(log_frame, weight=1, minsize=220)
            paned.pane(thumb_outer, weight=2, minsize=280)
        except tk.TclError:
            pass

        self.thumb_canvas = tk.Canvas(thumb_outer, highlightthickness=0, bg="#ffffff")
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

        # Gestion de la molette de la souris
        self.thumb_canvas.bind(
            "<Enter>",
            lambda _e: self.thumb_canvas.bind_all("<MouseWheel>", self._on_thumb_mousewheel),
        )
        self.thumb_canvas.bind(
            "<Leave>",
            lambda _e: self.thumb_canvas.unbind_all("<MouseWheel>"),
        )

        self.detail_var = tk.StringVar(
            value="Cliquez sur « Générer miniatures » sous un dossier pour l’aperçu."
        )
        ttk.Label(thumb_outer, textvariable=self.detail_var, wraplength=450).pack(
            fill=tk.X, pady=(6, 0)
        )

        self._append_log("Journal prêt — lancez une analyse pour y voir la progression.")

    # ── [Le reste des méthodes inchangé...] ────────────────────────
    def _has_font(self, family: str) -> bool:
        try:
            from tkinter import font as tkfont
            return family in tkfont.families()
        except Exception:
            return False

    def _set_summary_text(self, text: str) -> None:
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert("1.0", text)
        self.update_idletasks()

    def _set_summary_idle(self) -> None:
        self._set_summary_text(
            "Sélectionnez un dossier source pour analyser son contenu.\n"
            "Cliquez sur 'Analyser' pour détecter les doublons et structurer le plan."
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
                f"Doublons        : {stats.duplicate_groups} groupes ({stats.duplicate_extras} fichiers en trop)",
                f"Déjà corrects   : {stats.already_correct}",
                f"À organiser     : {stats.to_organize}",
                f"Sans EXIF       : {stats.sans_exif}",
                f"Vidéos à bouger : {stats.videos_to_move}",
                f"Dates à aligner : {stats.sync_dates}",
                f"Parasites       : {stats.junk}",
                f"Erreurs         : {stats.errors}",
            ]
        else:
            lines += ["", "Analyse non lancée — métriques des doublons indisponibles."]
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

    def _on_thumb_canvas_configure(self, event) -> None:
        self.thumb_canvas.itemconfigure(self._thumb_window, width=event.width)

    def _on_thumb_mousewheel(self, event) -> None:
        self.thumb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _sync_event_state(self) -> None:
        label = self.structure_var.get()
        key = self._label_to_structure.get(label, FolderStructure.YEAR_MONTH_DAY.value)
        needs_event = key in (FolderStructure.YEAR_MONTH_EVENT.value, FolderStructure.YEAR_EVENT.value)
        self.event_entry.configure(state=tk.NORMAL if needs_event else tk.DISABLED)

    def _restore_last_paths(self) -> None:
        source = get_last_source_dir()
        if source and Path(source).is_dir():
            self.source_var.set(source)
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
            self._clear_thumbnails()
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
        if self._busy: return
        roots: list[Path] = []
        source = self.source_var.get().strip()
        if source and Path(source).is_dir(): roots.append(Path(source))
        dest = self.output_var.get().strip()
        if dest and Path(dest).is_dir():
            dest_path = Path(dest)
            if not any(r.resolve() == dest_path.resolve() for r in roots): roots.append(dest_path)
        if not roots:
            messagebox.showwarning("Aucun dossier", "Indiquez un dossier source ou destination.")
            return
        labels = "\n".join(f"• {r}/.pycture/cache.sqlite" for r in roots)
        if not messagebox.askyesno("Vider le cache", f"Supprimer le cache Pycture de :\n\n{labels}\n\nContinuer ?"): return
        cleared = 0
        for root in roots:
            if clear_folder_cache(root): cleared += 1
        if cleared: messagebox.showinfo("Cache vidé", f"Cache effacé pour {cleared} dossier(s).")
        else: messagebox.showinfo("Cache", "Aucun fichier de cache trouvé.")

    def _pick_photos_library(self) -> Path | None:
        """Sélectionne un paquet photothèque Apple (paquets macOS)."""
        pictures = Path.home() / "Pictures"
        initial = str(pictures) if pictures.is_dir() else None
        path = filedialog.askopenfilename(
            title="Choisir une photothèque Photos / Aperture / iPhoto",
            initialdir=initial,
            filetypes=[
                ("Photothèques Apple", "*.photoslibrary *.aplibrary *.photolibrary"),
                ("Photos", "*.photoslibrary"),
                ("Aperture", "*.aplibrary"),
                ("iPhoto / Aperture", "*.photolibrary"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if path:
            return Path(path)
        path = filedialog.askdirectory(
            title="Ou choisir le dossier .photoslibrary / .aplibrary / .photolibrary",
            initialdir=initial,
        )
        return Path(path) if path else None

    def _import_photos_library(self) -> None:
        if self._busy:
            return
        dest = self.output_var.get().strip()
        if not dest:
            messagebox.showwarning(
                "Destination obligatoire",
                "Choisissez d'abord un dossier de destination.",
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
                    "Destination invalide", f"Erreur creation : {exc}"
                )
                return
        if not dest_path.is_dir():
            messagebox.showerror(
                "Destination invalide", "Ce chemin n'est pas un dossier."
            )
            return

        lib_path = self._pick_photos_library()
        if not lib_path:
            return
        if not is_apple_media_library(lib_path):
            messagebox.showerror(
                "Photothèque invalide",
                "Sélectionnez un paquet :\n"
                "• Photos : Nom.photoslibrary\n"
                "• Aperture : Nom.aplibrary\n"
                "• iPhoto / Aperture : Nom.photolibrary\n"
                "(souvent dans Images / Pictures).\n\n"
                f"Reçu : {lib_path}",
            )
            return
        kind = library_kind(lib_path) or "?"
        try:
            if (
                dest_path.resolve() == lib_path.resolve()
                or lib_path.resolve() in dest_path.resolve().parents
            ):
                messagebox.showerror(
                    "Destination invalide",
                    "La destination ne peut pas être à l'intérieur de la photothèque.",
                )
                return
        except OSError:
            pass
        kind_label = {
            "photos": "Photos",
            "aperture": "Aperture",
            "photolibrary": "iPhoto / Aperture",
        }.get(kind, kind)
        if not messagebox.askyesno(
            "Exporter",
            f"Copier les originaux ({kind_label}) de :\n{lib_path}\n\n"
            f"vers :\n{dest_path} ?\n\n"
            "La photothèque ne sera pas modifiée.",
        ):
            return
        self._set_busy(True)
        self._start_job(
            f"Export photothèque {kind_label}",
            [("Export :", "Copie des originaux")],
        )
        self._append_log(f"{lib_path}\n→ {dest_path}")
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
            for path, err in result.errors[:50]: self._append_log(f"  {path.name} : {err}")
        self.progress["value"] = 0
        self._end_job("Export terminé." if result.copied else "Export : aucun fichier copié.")
        self._set_busy(False)
        if result.copied:
            self.source_var.set(str(result.destination))
            remember_paths(source_dir=str(result.destination))
            self._clear_thumbnails()
            self._refresh_inventory_async(result.destination)
            messagebox.showinfo("Export terminé", f"{len(result.copied)} fichiers copiés.")
        else:
            messagebox.showwarning("Aucun fichier", "Aucun original n'a été copié. Vérifiez vos permissions système.")

    def _append_log(self, text: str) -> None:
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.update_idletasks()

    def _clear_log(self) -> None:
        self.log.delete("1.0", tk.END)

    def _clear_thumbnails(self) -> None:
        for child in self.thumb_inner.winfo_children():
            child.destroy()
        self._thumb_photos.clear()
        self.detail_var.set(
            "Cliquez sur « Générer miniatures » sous un dossier pour l’aperçu."
        )

    def _generate_folder_thumbnails(self, which: str) -> None:
        """Charge les miniatures du dossier source ou destination (à la demande)."""
        if self._busy:
            return
        if which == "source":
            raw = self.source_var.get().strip()
            label = "Source"
        else:
            raw = self.output_var.get().strip()
            label = "Destination"
        if not raw:
            messagebox.showwarning(
                "Dossier manquant",
                f"Indiquez d’abord un dossier {label.lower()}.",
            )
            return
        root = Path(raw)
        if not root.is_dir():
            messagebox.showerror("Erreur", f"Dossier introuvable :\n{root}")
            return
        self._load_folder_thumbnails(root, title=f"Dossier {label}")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.preview_btn.configure(state=state)
        self.merge_preview_btn.configure(state=state)
        self.clear_cache_btn.configure(state=state)
        self.source_thumbs_btn.configure(state=state)
        self.dest_thumbs_btn.configure(state=state)
        if busy:
            self.apply_btn.configure(state=tk.DISABLED)
            self.merge_apply_btn.configure(state=tk.DISABLED)
        else:
            self.apply_btn.configure(
                state=tk.NORMAL if self._plan and self._plan.moves else tk.DISABLED
            )
            self.merge_apply_btn.configure(
                state=tk.NORMAL
                if self._merge_plan and self._merge_plan.to_merge
                else tk.DISABLED
            )

    def _start_job(self, title: str, phases: list[tuple[str, str]]) -> None:
        """Déclare un travail multi-étapes (préfixe message → libellé affiché)."""
        self._job_phases = phases
        self._job_phase_index = -1
        self._progress_log_milestone = -1
        self.progress["value"] = 0
        self.progress["maximum"] = 1
        self.status_var.set(title)
        self._append_log(f"\n=== {title} ===")
        if phases:
            # Entrer tout de suite dans l'étape 1 (évite « Étape 0 » pendant le scan)
            self._job_phase_index = 0
            label = phases[0][1]
            n = len(phases)
            self.phase_var.set(f"Étape 1 sur {n} — {label}")
            self._append_log(f"▶ Étape 1/{n} — {label}")
        else:
            self.phase_var.set(title)

    def _end_job(self, status: str = "Terminé.") -> None:
        self._job_phases = []
        self._job_phase_index = -1
        self._progress_log_milestone = -1
        self.progress["value"] = 0
        self.phase_var.set("")
        self.status_var.set(status)

    def _phase_index_for_message(self, message: str) -> int | None:
        for i, (prefix, _label) in enumerate(self._job_phases):
            if prefix == "" or message.startswith(prefix):
                return i
        return None

    def _progress_cb(self, current: int, total: int, message: str) -> None:
        def update() -> None:
            total_safe = max(total, 1)
            self.progress["maximum"] = total_safe
            self.progress["value"] = min(current, total_safe)

            phase_i = self._phase_index_for_message(message)
            if phase_i is not None and phase_i != self._job_phase_index:
                self._job_phase_index = phase_i
                self._progress_log_milestone = -1
                _prefix, label = self._job_phases[phase_i]
                n = len(self._job_phases)
                self.phase_var.set(f"Étape {phase_i + 1} sur {n} — {label}")
                self._append_log(f"▶ Étape {phase_i + 1}/{n} — {label}")

            if self._job_phases and self._job_phase_index >= 0:
                _prefix, label = self._job_phases[self._job_phase_index]
                n = len(self._job_phases)
                self.phase_var.set(
                    f"Étape {self._job_phase_index + 1} sur {n} — {label} "
                    f"({current}/{total_safe})"
                )
            elif self._job_phases:
                self.phase_var.set(f"Étape … sur {len(self._job_phases)}")

            self.status_var.set(message)

            # Toujours journaliser les messages de scan / démarrage d'étape
            is_scan = current == 0 or "scan" in message.lower() or "fichier(s)" in message
            if is_scan:
                self._append_log(f"  {message}")
                return

            # Sinon : jalons 10 % pour ne pas noyer le journal
            if total_safe <= 1:
                milestone = 100 if current >= 1 else 0
            else:
                milestone = min(100, int(100 * current / total_safe))
                milestone = (milestone // 10) * 10
            if milestone >= 10 and milestone > self._progress_log_milestone:
                self._progress_log_milestone = milestone
                self._append_log(f"  … {current}/{total_safe} ({milestone}%) — {message}")
            elif current >= total_safe and self._progress_log_milestone < 100:
                self._progress_log_milestone = 100
                self._append_log(f"  … {current}/{total_safe} (100%) — {message}")

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

    _MAX_THUMBS = 80

    def _load_folder_thumbnails(
        self, root: Path, limit: int | None = None, title: str = "Dossier"
    ) -> None:
        """Aperçu à la demande après clic sur « Générer miniatures »."""
        if limit is None:
            limit = self._MAX_THUMBS
        self._thumb_load_id += 1
        load_id = self._thumb_load_id
        self._clear_thumbnails()
        self.status_var.set(f"Chargement des miniatures ({title})…")
        self._append_log(f"Miniatures : scan de {root} (max {limit})")

        def worker() -> None:
            try:
                images = collect_media(root, include_videos=True)[:limit]
            except Exception:
                images = []
            items = [(p, p.name, "") for p in images]
            self.after(
                0,
                lambda: self._populate_thumbnails(load_id, items, title),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_thumbnails(self) -> None:
        """Annule un chargement de miniatures en cours."""
        self._thumb_load_id += 1
        self._clear_thumbnails()
        self.progress["value"] = 0
        self.status_var.set("Chargement miniatures annulé.")

    def _populate_thumbnails(
        self,
        load_id: int,
        items: list[tuple[Path, str, str]],
        title: str,
    ) -> None:
        if load_id != self._thumb_load_id:
            return

        if len(items) > self._MAX_THUMBS:
            items = items[: self._MAX_THUMBS]
            self._append_log(
                f"Miniatures limitées à {self._MAX_THUMBS} (aperçu partiel)."
            )

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
        self._set_busy(True)
        self._plan = None
        self._merge_plan = None
        self._start_job(
            "Analyse d'organisation",
            [
                ("Recherche des doublons", "Recherche des doublons"),
                ("Empreinte :", "Empreintes des doublons"),
                ("Analyse :", "Analyse des fichiers"),
            ],
        )

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
        self._append_log("\n=== Aperçu (aucune modification) ===\n")
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

        self._end_job("Aperçu terminé. Vérifiez le journal puis cliquez sur Appliquer.")
        self._set_busy(False)

    def _run_merge_preview(self) -> None:
        if self._busy:
            return
        options = self._merge_options_from_ui()
        if not options:
            return

        self._remember_current_paths()
        self._clear_log()
        self._set_busy(True)
        self._plan = None
        self._merge_plan = None
        self._start_job(
            "Analyse fusion",
            [
                ("Empreinte dest", "Indexation de la destination"),
                ("Analyse fusion", "Analyse des fichiers source"),
            ],
        )
        self._append_log(f"Source      : {options.source_dir}")
        self._append_log(f"Destination : {options.destination_dir}")
        self._append_log(
            "Le scan initial d’un gros volume peut prendre plusieurs minutes…"
        )

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
        self._append_log("\n=== Aperçu fusion (aucune modification) ===\n")
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

        self._set_busy(False)
        if plan.to_merge:
            self.merge_apply_btn.configure(state=tk.NORMAL)
            self._end_job(
                "Aperçu fusion terminé. Vérifiez le journal puis cliquez sur Fusionner."
            )
        else:
            self.merge_apply_btn.configure(state=tk.DISABLED)
            self._end_job("Aperçu fusion terminé — rien à fusionner.")

    def _run_apply(self) -> None:
        if self._busy or not self._plan:
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
        plan = self._plan
        self._start_job(
            "Application de l'organisation",
            [("", "Exécution des actions")],
        )

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
        plan = self._merge_plan
        clean_empty = self.clean_empty_var.get()
        source_root = self.source_var.get().strip()
        dest_root = self.output_var.get().strip()
        self._start_job(
            "Application de la fusion",
            [("", "Copie / déplacement des fichiers")],
        )

        def worker() -> None:
            try:
                logs = execute_merge_plan(
                    plan,
                    dry_run=False,
                    progress_cb=self._progress_cb,
                )
                removed: list[Path] = []
                if clean_empty:
                    # Source surtout en mode déplacer ; destination aussi par cohérence
                    for root in (source_root, dest_root):
                        if root and Path(root).is_dir():
                            removed.extend(remove_empty_dirs(Path(root), dry_run=False))
                self.after(0, lambda: self._on_merge_apply_done(logs, plan, removed))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_merge_apply_done(
        self,
        logs: list[str],
        plan: MergePlan,
        removed: list[Path] | None = None,
    ) -> None:
        for line in logs:
            self._append_log(line)

        errors = sum(1 for line in logs if "ERREUR" in line)
        self._append_log("\n--- Résumé fusion ---")
        self._append_log(f"Fusionnés : {len(plan.to_merge)}")
        self._append_log(f"  dont renommages conflit : {len(plan.renames)}")
        self._append_log(f"Ignorés (doublon contenu) : {len(plan.skipped)}")
        self._append_log(f"Erreurs : {errors}")
        if removed:
            self._append_log(f"\nDossiers vides supprimés : {len(removed)}")
            for d in removed[:50]:
                self._append_log(f"  {d}")
            if len(removed) > 50:
                self._append_log(f"  … et {len(removed) - 50} autres")
        self._append_log("\nTerminé.")

        dest = self.output_var.get().strip()
        if dest and Path(dest).is_dir():
            self._refresh_inventory_async(Path(dest))

        self._end_job("Fusion terminée.")
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
        self._end_job("Organisation terminée.")
        self._plan = None
        self._merge_plan = None
        self._set_busy(False)
        messagebox.showinfo("Terminé", "L'organisation des photos est terminée.")

    def _on_error(self, exc: Exception) -> None:
        self._append_log(f"\nERREUR : {exc}")
        self._end_job("Erreur.")
        self._set_busy(False)
        messagebox.showerror("Erreur", str(exc))


def run_app() -> None:
    app = PyctureApp()
    app.mainloop()
