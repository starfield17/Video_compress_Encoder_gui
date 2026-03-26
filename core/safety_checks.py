from __future__ import annotations

from pathlib import Path

from core.models import EncodeOptions, EncoderInfo
from core.path_utils import ensure_dir


def validate_workdir(workdir: Path) -> Path:
    workdir = workdir.expanduser().resolve()
    ensure_dir(workdir / "preview")
    ensure_dir(workdir / "logs")
    ensure_dir(workdir / "temp")
    return workdir


def validate_output_path(source_path: Path, output_path: Path, overwrite: bool) -> None:
    if source_path.resolve() == output_path.resolve():
        raise RuntimeError(f"Output path matches the input path, refusing to overwrite source: {source_path}")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists and overwrite is disabled: {output_path}")
    ensure_dir(output_path.parent)


def validate_two_pass(options: EncodeOptions, encoder_info: EncoderInfo) -> None:
    if options.two_pass and not encoder_info.supports_two_pass:
        raise RuntimeError(f"Encoder {encoder_info.encoder_name} does not support two-pass in this implementation.")


def validate_plan_item(
    source_path: Path,
    output_path: Path,
    options: EncodeOptions,
    encoder_info: EncoderInfo,
    workdir: Path,
) -> None:
    validate_workdir(workdir)
    validate_output_path(source_path, output_path, options.overwrite)
    validate_two_pass(options, encoder_info)
