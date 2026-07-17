"""Génération de miniatures pour l'aperçu GUI."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps, ImageTk


THUMB_SIZE = (120, 120)


def make_thumbnail(path: Path, size: tuple[int, int] = THUMB_SIZE) -> ImageTk.PhotoImage | None:
    """Charge une image et retourne une miniature Tk, ou None en cas d'échec."""
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail(size, Image.Resampling.LANCZOS)
            # Cadre fixe pour un alignement régulier
            canvas = Image.new("RGB", size, (40, 40, 40))
            offset = ((size[0] - img.width) // 2, (size[1] - img.height) // 2)
            canvas.paste(img, offset)
            return ImageTk.PhotoImage(canvas)
    except Exception:
        return None
