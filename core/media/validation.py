from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from core.models import EncodeOptions, EncoderInfo
from core.media.paths import ensure_dir


def validate_workdir(workdir: Path, *, create_directories: bool = True) -> Path:
    workdir = workdir.expanduser().resolve()
    if create_directories:
        ensure_dir(workdir / "logs")
        ensure_dir(workdir / "temp")
    return workdir


def validate_output_path(
    source_path: Path,
    output_path: Path,
    overwrite: bool,
    *,
    create_directories: bool = True,
) -> None:
    # Guard against accidentally destroying the source file (e.g. same input/output directory
    # combined with a codec suffix that collides with the original extension).
    if source_path.resolve() == output_path.resolve():
        raise RuntimeError(f"Output path matches the input path, refusing to overwrite source: {source_path}")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists and overwrite is disabled: {output_path}")
    if create_directories:
        ensure_dir(output_path.parent)


def normalized_output_path(path: Path) -> str:
    """Return the queue-wide comparison key for an output destination."""

    return os.path.normcase(str(path.expanduser().resolve())).casefold()


def validate_unique_output_paths(paths: Iterable[tuple[Path, Path]]) -> None:
    """Reject two different sources targeting the same output path."""

    seen: dict[str, tuple[Path, Path]] = {}
    for source_path, output_path in paths:
        key = normalized_output_path(output_path)
        previous = seen.get(key)
        if previous is not None:
            previous_source, previous_output = previous
            raise RuntimeError(
                "Queue output collision: "
                f"{previous_source} and {source_path} both target {previous_output}"
            )
        seen[key] = (source_path, output_path)


def validate_two_pass(options: EncodeOptions, encoder_info: EncoderInfo) -> None:
    if options.two_pass and not encoder_info.supports_two_pass:
        raise RuntimeError(f"Encoder {encoder_info.encoder_name} does not support two-pass in this implementation.")


def validate_plan_item(
    source_path: Path,
    output_path: Path,
    options: EncodeOptions,
    encoder_info: EncoderInfo,
    workdir: Path,
    *,
    create_directories: bool = True,
) -> None:
    # Run every pre-encode safety check for a single plan item before touching ffmpeg.
    validate_workdir(workdir, create_directories=create_directories)
    validate_output_path(
        source_path,
        output_path,
        options.overwrite,
        create_directories=create_directories,
    )
    validate_two_pass(options, encoder_info)
