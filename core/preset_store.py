from __future__ import annotations

import json
import re
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from core.app_paths import workdir_dir
from core.models import (
    AudioMode,
    BackendChoice,
    CodecChoice,
    ContainerChoice,
    DecodeAcceleration,
    EncodeOptions,
)


APP_CONFIG_NAME = "app_config.json"
# Reentrant lock so nested calls (e.g. updater that reads config) don't deadlock.
_APP_CONFIG_LOCK = RLock()


def presets_dir(config_dir: Path) -> Path:
    path = config_dir / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_config_path(config_dir: Path) -> Path:
    runtime_workdir = workdir_dir()
    runtime_workdir.mkdir(parents=True, exist_ok=True)
    return runtime_workdir / APP_CONFIG_NAME


def _preset_path(name: str, config_dir: Path) -> Path:
    # Validate preset name before constructing the path to prevent path traversal.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("Preset names may only contain letters, numbers, dots, underscores, and dashes.")
    return presets_dir(config_dir) / f"{name}.json"


def encode_options_to_preset_data(options: EncodeOptions) -> dict[str, Any]:
    return {
        "codec": options.codec.value,
        "backend": options.backend.value,
        "decode_acceleration": options.decode_acceleration.value,
        "parallel_enabled": options.parallel_enabled,
        "parallel_backends": [backend.value for backend in options.parallel_backends],
        "ratio": options.ratio,
        "min_video_kbps": options.min_video_kbps,
        "max_video_kbps": options.max_video_kbps,
        "container": options.container.value,
        "audio_mode": options.audio_mode.value,
        "audio_bitrate": options.audio_bitrate,
        "copy_subtitles": options.copy_subtitles,
        "copy_external_subtitles": options.copy_external_subtitles,
        "two_pass": options.two_pass,
        "preset": options.encoder_preset,
        "pix_fmt": options.pix_fmt,
        "maxrate_factor": options.maxrate_factor,
        "bufsize_factor": options.bufsize_factor,
    }


def validate_preset_schema(data: dict[str, Any]) -> dict[str, Any]:
    # Backfill keys added after the preset format was established, then validate.
    data = dict(data)
    if "copy_external_subtitles" not in data:
        data["copy_external_subtitles"] = False
    if "parallel_enabled" not in data:
        data["parallel_enabled"] = False
    if "parallel_backends" not in data:
        data["parallel_backends"] = []
    if "decode_acceleration" not in data:
        data["decode_acceleration"] = DecodeAcceleration.SOFTWARE.value

    required = {
        "codec",
        "backend",
        "decode_acceleration",
        "ratio",
        "min_video_kbps",
        "max_video_kbps",
        "container",
        "audio_mode",
        "audio_bitrate",
        "copy_subtitles",
        "copy_external_subtitles",
        "two_pass",
        "preset",
        "pix_fmt",
        "maxrate_factor",
        "bufsize_factor",
    }
    missing = required.difference(data)
    if missing:
        raise ValueError(f"Preset is missing fields: {', '.join(sorted(missing))}")

    # Constructing each enum validates the string value; raises ValueError on invalid input.
    CodecChoice(data["codec"])
    BackendChoice(data["backend"])
    DecodeAcceleration(data["decode_acceleration"])
    for backend in data["parallel_backends"]:
        BackendChoice(backend)
    ContainerChoice(data["container"])
    AudioMode(data["audio_mode"])
    if data["ratio"] is not None and float(data["ratio"]) <= 0:
        raise ValueError("ratio must be greater than 0")
    return data


def preset_data_to_encode_options(data: dict[str, Any]) -> EncodeOptions:
    data = validate_preset_schema(data)
    preset_value = data.get("preset")
    normalized_preset = str(preset_value).strip() if preset_value is not None else ""
    return EncodeOptions(
        codec=CodecChoice(data["codec"]),
        backend=BackendChoice(data["backend"]),
        decode_acceleration=DecodeAcceleration(data["decode_acceleration"]),
        parallel_enabled=bool(data.get("parallel_enabled", False)),
        parallel_backends=tuple(BackendChoice(item) for item in data.get("parallel_backends", [])),
        ratio=None if data["ratio"] is None else float(data["ratio"]),
        min_video_kbps=int(data["min_video_kbps"]),
        max_video_kbps=int(data["max_video_kbps"]),
        container=ContainerChoice(data["container"]),
        audio_mode=AudioMode(data["audio_mode"]),
        audio_bitrate=str(data["audio_bitrate"]),
        copy_subtitles=bool(data["copy_subtitles"]),
        copy_external_subtitles=bool(data.get("copy_external_subtitles", False)),
        two_pass=bool(data["two_pass"]),
        encoder_preset=normalized_preset or None,
        pix_fmt=str(data["pix_fmt"]),
        maxrate_factor=float(data["maxrate_factor"]),
        bufsize_factor=float(data["bufsize_factor"]),
    )


def list_presets(config_dir: Path) -> list[str]:
    return sorted(path.stem for path in presets_dir(config_dir).glob("*.json"))


def load_preset(name: str, config_dir: Path) -> EncodeOptions:
    path = _preset_path(name, config_dir)
    if not path.exists():
        raise FileNotFoundError(f"Preset does not exist: {name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return preset_data_to_encode_options(data)


def save_preset(name: str, options: EncodeOptions, config_dir: Path) -> Path:
    path = _preset_path(name, config_dir)
    data = encode_options_to_preset_data(options)
    validate_preset_schema(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def delete_preset(name: str, config_dir: Path) -> None:
    path = _preset_path(name, config_dir)
    if not path.exists():
        raise FileNotFoundError(f"Preset does not exist: {name}")
    path.unlink()


def _default_app_config() -> dict[str, Any]:
    return {
        "default_preset_name": "default_hevc",
        "keep_preview_temp": True,
        "recent_paths": [],
        "log_level": "info",
        "language": "en",
    }


def _load_app_config_unlocked(config_dir: Path) -> dict[str, Any]:
    path = app_config_path(config_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    return _default_app_config()


def _save_app_config_unlocked(config_dir: Path, data: dict[str, Any]) -> Path:
    path = app_config_path(config_dir)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_app_config(config_dir: Path) -> dict[str, Any]:
    with _APP_CONFIG_LOCK:
        return _load_app_config_unlocked(config_dir)


def save_app_config(config_dir: Path, data: dict[str, Any]) -> Path:
    with _APP_CONFIG_LOCK:
        return _save_app_config_unlocked(config_dir, data)


def update_app_config(
    config_dir: Path,
    updater: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> Path:
    # Atomically read-modify-write. Returning None means the updater mutated
    # the loaded dict in place; returning a dict replaces it entirely.
    with _APP_CONFIG_LOCK:
        data = _load_app_config_unlocked(config_dir)
        updated = updater(data)
        if updated is not None:
            data = updated
        return _save_app_config_unlocked(config_dir, data)
