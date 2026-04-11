from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from core.models import BackendChoice, CodecChoice, EncoderInfo


FALLBACK_PRESET_CHOICES: dict[str, tuple[str, ...]] = {
    "libx265": (
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
        "placebo",
    ),
    "hevc_nvenc": ("p1", "p2", "p3", "p4", "p5", "p6", "p7"),
    "av1_nvenc": ("p1", "p2", "p3", "p4", "p5", "p6", "p7"),
    "hevc_qsv": ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"),
    "av1_qsv": ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"),
}
QUALITY_PRESET_KEYWORD = "quality"
PRESET_LINE_RE = re.compile(r"^\s*-preset(?:\s|$)")
OPTION_LINE_RE = re.compile(r"^\s*-[A-Za-z0-9]")
PRESET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")


def list_available_encoders(ffmpeg_path: Path) -> set[str]:
    proc = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-encoders"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    encoders: set[str] = set()
    pattern = re.compile(r"^\s*[A-Z\.]{6}\s+([^\s]+)")
    for line in proc.stdout.splitlines():
        match = pattern.match(line)
        if match:
            encoders.add(match.group(1))
    return encoders


def _fallback_preset_choices(encoder_name: str) -> list[str]:
    return list(FALLBACK_PRESET_CHOICES.get(encoder_name, ()))


def _run_encoder_help(ffmpeg_path: Path, encoder_name: str) -> str:
    proc = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-h", f"encoder={encoder_name}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return "\n".join(part for part in (proc.stdout, proc.stderr) if part)


def _looks_like_preset_value(token: str) -> bool:
    normalized = token.strip()
    if not normalized or normalized in {"default", "from", "to"}:
        return False
    if normalized.startswith("<") or normalized.startswith("("):
        return False
    return PRESET_TOKEN_RE.fullmatch(normalized) is not None


def _extract_preset_choices(help_text: str) -> list[str]:
    lines = help_text.splitlines()
    start_index = next((index for index, line in enumerate(lines) if PRESET_LINE_RE.search(line)), -1)
    if start_index < 0:
        return []

    choices: list[str] = []
    for line in lines[start_index + 1 :]:
        if OPTION_LINE_RE.match(line):
            break
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if _looks_like_preset_value(token) and token not in choices:
            choices.append(token)
    return choices


@lru_cache(maxsize=64)
def _cached_runtime_preset_choices(ffmpeg_path: Path, encoder_name: str) -> tuple[str, ...]:
    output = _run_encoder_help(ffmpeg_path, encoder_name)
    return tuple(_extract_preset_choices(output))


def preset_choices_for_encoder(ffmpeg_path: Path, encoder_name: str) -> list[str]:
    choices = list(_cached_runtime_preset_choices(ffmpeg_path, encoder_name))
    if choices:
        return choices
    return _fallback_preset_choices(encoder_name)


def is_valid_preset(ffmpeg_path: Path, encoder_name: str, preset: str) -> bool:
    normalized = preset.strip()
    if not normalized:
        return False
    return normalized in preset_choices_for_encoder(ffmpeg_path, encoder_name)


def _quality_preset_from_choices(choices: list[str]) -> str | None:
    for choice in choices:
        if QUALITY_PRESET_KEYWORD in choice.lower():
            return choice
    return None


def default_preset_for_encoder(encoder_name: str, ffmpeg_path: Path | None = None) -> str | None:
    if encoder_name == "libx265":
        return "slow"
    if encoder_name == "libsvtav1":
        return "5"
    if encoder_name in {"hevc_nvenc", "av1_nvenc"}:
        return "p6"
    if encoder_name in {"hevc_qsv", "av1_qsv"}:
        return "slow"
    if encoder_name in {"hevc_amf", "av1_amf"} and ffmpeg_path is not None:
        return _quality_preset_from_choices(preset_choices_for_encoder(ffmpeg_path, encoder_name))
    return None


def resolve_encoder(
    codec: CodecChoice,
    backend: BackendChoice,
    available_encoders: set[str],
    ffmpeg_path: Path | None = None,
) -> EncoderInfo:
    cpu_map = {
        CodecChoice.HEVC: "libx265",
        CodecChoice.AV1: "libsvtav1",
    }
    nvenc_map = {
        CodecChoice.HEVC: "hevc_nvenc",
        CodecChoice.AV1: "av1_nvenc",
    }
    qsv_map = {
        CodecChoice.HEVC: "hevc_qsv",
        CodecChoice.AV1: "av1_qsv",
    }
    amf_map = {
        CodecChoice.HEVC: "hevc_amf",
        CodecChoice.AV1: "av1_amf",
    }
    backend_maps = {
        BackendChoice.CPU: cpu_map,
        BackendChoice.NVENC: nvenc_map,
        BackendChoice.QSV: qsv_map,
        BackendChoice.AMF: amf_map,
    }

    if backend == BackendChoice.AUTO:
        for candidate_backend in (BackendChoice.NVENC, BackendChoice.QSV, BackendChoice.AMF, BackendChoice.CPU):
            encoder_name = backend_maps[candidate_backend][codec]
            if encoder_name in available_encoders:
                return EncoderInfo(
                    codec=codec,
                    backend=candidate_backend,
                    encoder_name=encoder_name,
                    supports_two_pass=encoder_name == "libx265",
                    default_preset=default_preset_for_encoder(encoder_name, ffmpeg_path),
                )
        raise RuntimeError(f"No available {codec.value} encoder was found in the current FFmpeg build.")

    encoder_name = backend_maps[backend][codec]
    if encoder_name not in available_encoders:
        raise RuntimeError(f"Requested encoder {encoder_name} is not available in the current FFmpeg build.")
    return EncoderInfo(
        codec=codec,
        backend=backend,
        encoder_name=encoder_name,
        supports_two_pass=encoder_name == "libx265",
        default_preset=default_preset_for_encoder(encoder_name, ffmpeg_path),
    )
