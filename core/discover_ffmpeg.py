from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.app_paths import app_root, bundle_root


PROJECT_FFMPEG_DIRNAME = "FFmpeg"
PROJECT_BINARY_SEARCH_DIRS = (
    Path(),
    Path("bin"),
    Path("current") / "bin",
)
COMMON_HOMEBREW_PREFIXES = (
    Path("/opt/homebrew"),
    Path("/usr/local"),
    Path("/home/linuxbrew/.linuxbrew"),
)


def is_windows() -> bool:
    return os.name == "nt"


def candidate_binary_names(binary_name: str) -> tuple[str, ...]:
    if is_windows():
        return (f"{binary_name}.exe", binary_name)
    return (binary_name,)


def _resolve_existing_file(candidate: Path) -> Optional[Path]:
    if candidate.exists() and candidate.is_file():
        return candidate.resolve()
    return None


def project_ffmpeg_dirs() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in (app_root(), bundle_root()):
        directory = (root / PROJECT_FFMPEG_DIRNAME).resolve()
        if directory in seen:
            continue
        seen.add(directory)
        candidates.append(directory)
    return candidates


def detect_project_binary(binary_name: str) -> Optional[Path]:
    for base_dir in project_ffmpeg_dirs():
        for relative_dir in PROJECT_BINARY_SEARCH_DIRS:
            for name in candidate_binary_names(binary_name):
                candidate = base_dir / relative_dir / name
                resolved = _resolve_existing_file(candidate)
                if resolved is not None:
                    return resolved
    return None


def detect_path_binary(binary_name: str) -> Optional[Path]:
    for name in candidate_binary_names(binary_name):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


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
        for search_dir in PROJECT_BINARY_SEARCH_DIRS:
            for name in candidate_binary_names(binary_name):
                candidate = Path(prefix) / search_dir / name
                resolved = _resolve_existing_file(candidate)
                if resolved is not None:
                    return resolved
    except OSError:
        return None
    return None


def detect_homebrew_binary(binary_name: str) -> Optional[Path]:
    brew = shutil.which("brew")
    if brew:
        try:
            proc = subprocess.run(
                [brew, "--prefix", "ffmpeg"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0:
                prefix = proc.stdout.strip().strip('"')
                if prefix:
                    for name in candidate_binary_names(binary_name):
                        candidate = Path(prefix) / "bin" / name
                        resolved = _resolve_existing_file(candidate)
                        if resolved is not None:
                            return resolved
        except OSError:
            pass

    for prefix in COMMON_HOMEBREW_PREFIXES:
        for name in candidate_binary_names(binary_name):
            candidate = prefix / "bin" / name
            resolved = _resolve_existing_file(candidate)
            if resolved is not None:
                return resolved
    return None


def find_binary(user_path: Optional[str], binary_name: str) -> Path:
    if user_path:
        candidate = Path(user_path).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(f"Cannot find the specified {binary_name}: {candidate}")

    project_binary = detect_project_binary(binary_name)
    if project_binary is not None:
        return project_binary

    path_binary = detect_path_binary(binary_name)
    if path_binary is not None:
        return path_binary

    platform_binary = detect_scoop_ffmpeg(binary_name) if is_windows() else detect_homebrew_binary(binary_name)
    if platform_binary is not None:
        return platform_binary

    raise FileNotFoundError(
        f"Cannot find {binary_name}. Checked explicit path, project '{PROJECT_FFMPEG_DIRNAME}/', PATH, "
        "and platform-specific locations."
    )


def discover_ffmpeg_tools(
    ffmpeg_path: Optional[str] = None,
    ffprobe_path: Optional[str] = None,
) -> tuple[Path, Path]:
    return find_binary(ffmpeg_path, "ffmpeg"), find_binary(ffprobe_path, "ffprobe")
