"""Persistance des préférences utilisateur (dernier chemin, etc.)."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".pycture"
CONFIG_FILE = CONFIG_DIR / "settings.json"


def load_settings() -> dict:
    try:
        if CONFIG_FILE.is_file():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_settings(settings: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def get_last_source_dir() -> str:
    return str(load_settings().get("last_source_dir", "") or "")


def get_last_output_dir() -> str:
    return str(load_settings().get("last_output_dir", "") or "")


def remember_paths(source_dir: str | None = None, output_dir: str | None = None) -> None:
    settings = load_settings()
    if source_dir is not None:
        settings["last_source_dir"] = source_dir
    if output_dir is not None:
        settings["last_output_dir"] = output_dir
    save_settings(settings)
