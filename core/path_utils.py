from __future__ import annotations

import hashlib
import re
from pathlib import Path

from core.models import CodecChoice, ContainerChoice


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def choose_output_root(input_path: Path, output_dir: Path | None, codec: CodecChoice) -> Path:
    if output_dir:
        return output_dir.expanduser().resolve()
    input_path = input_path.expanduser().resolve()
    if input_path.is_file():
        return input_path.parent
    return (input_path.parent / f"{input_path.name}_compressed_{codec.value}").resolve()


def build_output_path(
    source_path: Path,
    input_root: Path,
    output_root: Path,
    codec: CodecChoice,
    container: ContainerChoice,
) -> Path:
    if input_root.is_dir():
        try:
            relative_parent = source_path.parent.relative_to(input_root)
        except ValueError:
            relative_parent = Path()
    else:
        relative_parent = Path()
    destination_dir = ensure_dir(output_root / relative_parent)
    return destination_dir / f"{source_path.stem}_{codec.value}.{container.value}"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "item"


def _source_token(source_path: Path) -> str:
    digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()[:10]
    return f"{_safe_name(source_path.stem)}_{digest}"


def preview_paths(
    workdir: Path,
    source_path: Path,
    codec: CodecChoice,
    container: ContainerChoice,
) -> tuple[Path, Path]:
    token = _source_token(source_path)
    preview_root = ensure_dir(workdir / "preview" / token)
    source_sample_path = preview_root / f"{source_path.stem}_source_sample{source_path.suffix}"
    encoded_sample_path = preview_root / f"{source_path.stem}_{codec.value}_preview.{container.value}"
    return source_sample_path, encoded_sample_path


def log_file_path(workdir: Path, source_path: Path, stage: str) -> Path:
    log_root = ensure_dir(workdir / "logs")
    return log_root / f"{_source_token(source_path)}_{stage}.log"


def passlog_prefix(workdir: Path, source_path: Path, stage: str) -> Path:
    temp_root = ensure_dir(workdir / "temp")
    return temp_root / f"{_source_token(source_path)}_{stage}.ffpass"
