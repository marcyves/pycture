"""Génération de miniatures pour l'aperçu GUI."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, ImageTk

from .exif_utils import is_video

THUMB_SIZE = (120, 120)


def make_video_placeholder(path: Path, size: tuple[int, int] = THUMB_SIZE) -> ImageTk.PhotoImage:
    """Pastille « VIDEO » pour les fichiers sans miniature image."""
    canvas = Image.new("RGB", size, (32, 36, 48))
    draw = ImageDraw.Draw(canvas)
    label = path.suffix.upper().lstrip(".") or "VIDEO"
    text = f"▶ {label}"
    # Centrage approximatif sans police custom
    bbox = draw.textbbox((0, 0), text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size[0] - tw) // 2, (size[1] - th) // 2), text, fill=(220, 220, 230))
    return ImageTk.PhotoImage(canvas)


def make_thumbnail(path: Path, size: tuple[int, int] = THUMB_SIZE) -> ImageTk.PhotoImage | None:
    """Charge une image / pastille vidéo et retourne une miniature Tk."""
    if is_video(path):
        try:
            return make_video_placeholder(path, size)
        except Exception:
            return None
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail(size, Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", size, (40, 40, 40))
            offset = ((size[0] - img.width) // 2, (size[1] - img.height) // 2)
            canvas.paste(img, offset)
            return ImageTk.PhotoImage(canvas)
    except Exception:
        return None
