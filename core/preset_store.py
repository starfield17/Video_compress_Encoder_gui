from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.app_paths import workdir_dir
from core.models import (
    AudioMode,
    BackendChoice,
    CodecChoice,
    ContainerChoice,
    EncodeOptions,
)


APP_CONFIG_NAME = "app_config.json"


def presets_dir(config_dir: Path) -> Path:
    path = config_dir / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_config_path(config_dir: Path) -> Path:
    runtime_workdir = workdir_dir()
    runtime_workdir.mkdir(parents=True, exist_ok=True)
    return runtime_workdir / APP_CONFIG_NAME


def _preset_path(name: str, config_dir: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("Preset names may only contain letters, numbers, dots, underscores, and dashes.")
    return presets_dir(config_dir) / f"{name}.json"


def encode_options_to_preset_data(options: EncodeOptions) -> dict[str, Any]:
    return {
        "codec": options.codec.value,
        "backend": options.backend.value,
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
    data = dict(data)
    if "copy_external_subtitles" not in data:
        data["copy_external_subtitles"] = False
    if "parallel_enabled" not in data:
        data["parallel_enabled"] = False
    if "parallel_backends" not in data:
        data["parallel_backends"] = []

    required = {
        "codec",
        "backend",
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

    CodecChoice(data["codec"])
    BackendChoice(data["backend"])
    for backend in data["parallel_backends"]:
        BackendChoice(backend)
    ContainerChoice(data["container"])
    AudioMode(data["audio_mode"])
    if data["ratio"] is not None and float(data["ratio"]) <= 0:
        raise ValueError("ratio must be greater than 0")
    return data


def preset_data_to_encode_options(data: dict[str, Any]) -> EncodeOptions:
    validate_preset_schema(data)
    preset_value = data.get("preset")
    normalized_preset = str(preset_value).strip() if preset_value is not None else ""
    return EncodeOptions(
        codec=CodecChoice(data["codec"]),
        backend=BackendChoice(data["backend"]),
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


def load_app_config(config_dir: Path) -> dict[str, Any]:
    path = app_config_path(config_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    return {
        "default_preset_name": "default_hevc",
        "keep_preview_temp": True,
        "recent_paths": [],
        "log_level": "info",
        "language": "en",
    }


def save_app_config(config_dir: Path, data: dict[str, Any]) -> Path:
    path = app_config_path(config_dir)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
