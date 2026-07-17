"""Interface graphique Pycture — nettoyage et organisation de photos."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .organizer import (
    DuplicateAction,
    FolderStructure,
    OrganizerOptions,
    OrganizerPlan,
    build_plan,
    collect_images,
    execute_plan,
    remove_empty_dirs,
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
        self.minsize(960, 680)
        self.geometry("1100x760")

        self._plan: OrganizerPlan | None = None
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

        # Dossier source
        src_frame = ttk.LabelFrame(main, text="Dossier de travail", padding=10)
        src_frame.pack(fill=tk.X, **pad)

        self.source_var = tk.StringVar()
        ttk.Entry(src_frame, textvariable=self.source_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(src_frame, text="Parcourir…", command=self._browse_source).pack(
            side=tk.LEFT
        )

        # Dossier de sortie (optionnel)
        out_frame = ttk.LabelFrame(
            main,
            text="Dossier de destination (vide = réorganiser sur place)",
            padding=10,
        )
        out_frame.pack(fill=tk.X, **pad)

        self.output_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.output_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )
        ttk.Button(out_frame, text="Parcourir…", command=self._browse_output).pack(
            side=tk.LEFT
        )

        # Options
        opts = ttk.LabelFrame(main, text="Options", padding=10)
        opts.pack(fill=tk.X, **pad)

        ttk.Label(opts, text="Structure des dossiers :").grid(
            row=0, column=0, sticky=tk.W, pady=4
        )
        self.structure_var = tk.StringVar()
        structure_combo = ttk.Combobox(
            opts,
            textvariable=self.structure_var,
            state="readonly",
            width=36,
        )
        structure_combo.grid(row=0, column=1, sticky=tk.W, pady=4, padx=8)
        structure_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_event_state())

        self._structure_labels = {
            FolderStructure.YEAR_MONTH_DAY.value: "année / mois / jour",
            FolderStructure.YEAR_MONTH_EVENT.value: "année / mois / événement",
            FolderStructure.YEAR_EVENT.value: "année / événement",
        }
        structure_combo.configure(values=list(self._structure_labels.values()))
        self.structure_var.set(self._structure_labels[FolderStructure.YEAR_MONTH_DAY.value])
        self._label_to_structure = {v: k for k, v in self._structure_labels.items()}

        ttk.Label(opts, text="Nom de l'événement :").grid(
            row=1, column=0, sticky=tk.W, pady=4
        )
        self.event_var = tk.StringVar()
        self.event_entry = ttk.Entry(opts, textvariable=self.event_var, width=38)
        self.event_entry.grid(row=1, column=1, sticky=tk.W, pady=4, padx=8)

        self.rename_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="Renommer les photos avec la date/heure (aaaa-mm-jj hh-mm-ss)",
            variable=self.rename_var,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=4)

        ttk.Label(opts, text="Doublons (contenu identique) :").grid(
            row=3, column=0, sticky=tk.W, pady=4
        )
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
            width=36,
            values=list(self._dup_labels.values()),
        ).grid(row=3, column=1, sticky=tk.W, pady=4, padx=8)

        self.clean_empty_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="Supprimer les dossiers vides après organisation",
            variable=self.clean_empty_var,
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=4)

        self.clean_junk_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="Supprimer les fichiers parasites macOS (._* , .DS_Store, …)",
            variable=self.clean_junk_var,
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=4)

        opts.columnconfigure(1, weight=1)

        # Actions
        actions = ttk.Frame(main)
        actions.pack(fill=tk.X, **pad)

        self.preview_btn = ttk.Button(
            actions, text="Analyser (aperçu)", command=self._run_preview
        )
        self.preview_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.apply_btn = ttk.Button(
            actions, text="Appliquer", command=self._run_apply, state=tk.DISABLED
        )
        self.apply_btn.pack(side=tk.LEFT)

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

    def _browse_output(self) -> None:
        initial = self.output_var.get().strip() or self.source_var.get().strip() or None
        path = filedialog.askdirectory(
            title="Choisir le dossier de destination",
            initialdir=initial if initial and Path(initial).is_dir() else None,
        )
        if path:
            self.output_var.set(path)
            remember_paths(output_dir=path)

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
        if busy:
            self.apply_btn.configure(state=tk.DISABLED)
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
                images = collect_images(root)[:limit]
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
            if move.reason == "suppression":
                caption = move.source.name
                badge = "suppression"
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
        if self._plan:
            for move in self._plan.moves:
                if move.source.resolve() == path.resolve():
                    if move.reason == "suppression":
                        parts.append("Action : suppression (doublon)")
                    elif move.reason == "junk":
                        parts.append("Action : suppression (fichier parasite macOS)")
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

        def worker() -> None:
            try:
                plan = build_plan(options, progress_cb=self._progress_cb)
                self.after(0, lambda: self._on_preview_done(plan, options))
            except Exception as exc:
                self.after(0, lambda: self._on_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_preview_done(self, plan: OrganizerPlan, options: OrganizerOptions) -> None:
        self._plan = plan
        self._clear_log()
        self._append_log("=== Aperçu (aucune modification) ===\n")
        self._append_log(plan.summary)
        self._append_log("")

        if plan.duplicate_groups:
            self._append_log("--- Doublons détectés ---")
            for g in plan.duplicate_groups:
                self._append_log(f"  Conservé : {g.keeper}")
                for d in g.duplicates:
                    self._append_log(f"  Doublon  : {d}")
            self._append_log("")

        self._append_log("--- Actions prévues ---")
        for move in plan.moves[:200]:
            if move.reason == "suppression":
                self._append_log(f"SUPPRIMER  {move.source}")
            elif move.reason == "junk":
                self._append_log(f"PARASITE   {move.source}")
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
        self._append_log("\n=== Application ===\n")
        plan = self._plan

        def worker() -> None:
            try:
                logs = execute_plan(plan, dry_run=False, progress_cb=self._progress_cb)
                removed: list[Path] = []
                if self.clean_empty_var.get():
                    root = (options.output_dir or options.source_dir).resolve()
                    removed = remove_empty_dirs(root, dry_run=False)
                self.after(0, lambda: self._on_apply_done(logs, removed))
            except Exception as exc:
                self.after(0, lambda: self._on_error(exc))

        threading.Thread(target=worker, daemon=True).start()

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
