"""Media metadata normalization helpers shared by probing and encoding."""

from __future__ import annotations

import re


_BIT_DEPTH_SUFFIX_RE = re.compile(r"(\d{1,2})(?:le|be)?$")


def infer_bit_depth_from_pix_fmt(pix_fmt: str | None) -> int | None:
    """Infer a pixel format's bit depth when FFmpeg omits raw sample depth."""
    if not pix_fmt:
        return None
    normalized = pix_fmt.strip().lower()
    if normalized in {
        "yuv420p",
        "yuv422p",
        "yuv444p",
        "yuvj420p",
        "yuvj422p",
        "yuvj444p",
        "nv12",
        "nv21",
        "rgb24",
        "bgr24",
    }:
        return 8
    if normalized in {"p010", "p010le", "p010be", "p210", "p210le", "p210be"}:
        return 10
    match = _BIT_DEPTH_SUFFIX_RE.search(normalized)
    if match:
        value = int(match.group(1))
        return value if value in {9, 10, 12, 14, 16} else None
    return None
