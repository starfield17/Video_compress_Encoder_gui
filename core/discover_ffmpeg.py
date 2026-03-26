from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def is_windows() -> bool:
    return os.name == "nt"


def detect_scoop_ffmpeg(binary_name: str) -> Optional[Path]:
    scoop = shutil.which("scoop")
    if not scoop:
        return None
    try:
        proc = subprocess.run(
            [scoop, "prefix", "ffmpeg"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return None
        prefix = proc.stdout.strip().strip('"')
        if not prefix:
            return None
        candidates = [
            Path(prefix) / "bin" / f"{binary_name}.exe",
            Path(prefix) / f"{binary_name}.exe",
            Path(prefix) / "current" / "bin" / f"{binary_name}.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
    except OSError:
        return None
    return None


def find_binary(user_path: Optional[str], binary_name: str) -> Path:
    if user_path:
        candidate = Path(user_path).expanduser()
        if candidate.exists():
            return candidate.resolve()
        raise FileNotFoundError(f"Cannot find the specified {binary_name}: {candidate}")

    found = shutil.which(binary_name)
    if found:
        return Path(found).resolve()

    if is_windows():
        scoop_bin = detect_scoop_ffmpeg(binary_name)
        if scoop_bin:
            return scoop_bin

    raise FileNotFoundError(
        f"Cannot find {binary_name}. Make sure it is available on PATH or pass an explicit path."
    )


def discover_ffmpeg_tools(
    ffmpeg_path: Optional[str] = None,
    ffprobe_path: Optional[str] = None,
) -> tuple[Path, Path]:
    return find_binary(ffmpeg_path, "ffmpeg"), find_binary(ffprobe_path, "ffprobe")
