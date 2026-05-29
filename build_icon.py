"""
Build AERM_icon.ico with all standard Windows sizes for Explorer (incl. extra-large / 256px).

Usage: python build_icon.py
"""
from __future__ import annotations

import os
import sys

from PIL import Image

SOURCE_PNG = "AERM_icon.png"
OUTPUT_ICO = "AERM_icon.ico"

# Windows shell: 16–256 (256 = Extra large icons view); 24/96 help HiDPI scaling.
ICO_SIZES = [
    (256, 256),
    (128, 128),
    (96, 96),
    (64, 64),
    (48, 48),
    (32, 32),
    (24, 24),
    (16, 16),
]

# Upscale small sources before downscaling so 256px stays sharp.
MASTER_SIDE = 1024


def _square_crop_rgba(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def build_ico(
    source_path: str = SOURCE_PNG,
    output_path: str = OUTPUT_ICO,
    master_side: int = MASTER_SIDE,
) -> str:
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Missing source icon: {source_path}")

    src = _square_crop_rgba(Image.open(source_path))
    if max(src.size) < master_side:
        src = src.resize((master_side, master_side), Image.Resampling.LANCZOS)
    elif src.size[0] != master_side:
        src = src.resize((master_side, master_side), Image.Resampling.LANCZOS)

    src.save(output_path, format="ICO", sizes=ICO_SIZES)

    ico = Image.open(output_path)
    embedded = sorted(ico.info.get("sizes", []))
    print(f"Wrote {output_path} ({os.path.getsize(output_path)} bytes)")
    print(f"Embedded sizes: {embedded}")
    if (256, 256) not in embedded:
        print("Warning: 256x256 missing — extra-large Explorer icons may look soft.", file=sys.stderr)
    return output_path


if __name__ == "__main__":
    build_ico()
