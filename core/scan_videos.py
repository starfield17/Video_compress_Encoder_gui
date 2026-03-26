from __future__ import annotations

from pathlib import Path

from core.models import VideoFileItem


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
    ".mts",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".ogv",
}


def collect_video_files(input_path: Path, recursive: bool) -> list[VideoFileItem]:
    input_path = input_path.expanduser().resolve()
    if input_path.is_file():
        if input_path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"Input file is not a supported video format: {input_path}")
        return [VideoFileItem(path=input_path, relative_path=Path(input_path.name))]

    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    iterator = input_path.rglob("*") if recursive else input_path.glob("*")
    files = [
        VideoFileItem(path=path.resolve(), relative_path=path.resolve().relative_to(input_path))
        for path in iterator
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    files.sort(key=lambda item: str(item.relative_path).lower())
    return files
