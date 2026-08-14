#!/usr/bin/env python3
"""Prepare the supplied OathCast artwork as a transparent web logo.

Neutral near-black pixels become transparent while red-dominant pixels retain
their opacity.  This preserves the scarlet texture while letting the original
black matte disappear into the UI's pure-black background.  The script is
deterministic and never overwrites the source image.

Developer utility only; install the ``logo-tools`` project extra before use.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image


DEFAULT_SIZE = 192
TRANSPARENT_RED_SIGNAL = 2.0
OPAQUE_RED_SIGNAL = 24.0
VISIBLE_HIGHLIGHT_LUMINANCE = 105.0
OPAQUE_HIGHLIGHT_LUMINANCE = 185.0


def _smoothstep(value: float, start: float, end: float) -> float:
    if value <= start:
        return 0.0
    if value >= end:
        return 1.0
    normalized = (value - start) / (end - start)
    return normalized * normalized * (3.0 - 2.0 * normalized)


def prepare_logo(source: Path, output: Path, *, size: int = DEFAULT_SIZE) -> None:
    if size <= 0:
        raise ValueError("size must be positive")

    with Image.open(source) as opened:
        image = opened.convert("RGBA")
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        offset = ((size - image.width) // 2, (size - image.height) // 2)
        canvas.alpha_composite(image, offset)

    pixels = []
    source_pixels = canvas.get_flattened_data()
    for red, green, blue, alpha in source_pixels:
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        red_dominance = max(0.0, red - max(green, blue))
        # Weight color separation by actual red energy.  This removes noisy,
        # almost-black chroma while retaining genuinely dark scarlet texture.
        red_signal = red_dominance * math.sqrt(red / 255.0) if red else 0.0
        red_opacity = _smoothstep(
            red_signal,
            TRANSPARENT_RED_SIGNAL,
            OPAQUE_RED_SIGNAL,
        )
        highlight_opacity = _smoothstep(
            luminance,
            VISIBLE_HIGHLIGHT_LUMINANCE,
            OPAQUE_HIGHLIGHT_LUMINANCE,
        )
        matte = round(255 * max(red_opacity, highlight_opacity))
        pixels.append((red, green, blue, min(alpha, matte)))

    canvas.putdata(pixels)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="WEBP", lossless=True, method=6)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    args = parser.parse_args()
    prepare_logo(args.source, args.output, size=args.size)


if __name__ == "__main__":
    main()
