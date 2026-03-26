from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core.models import BackendChoice, CodecChoice, EncoderInfo


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


def default_preset_for_encoder(encoder_name: str) -> str | None:
    if encoder_name == "libx265":
        return "medium"
    if encoder_name == "libsvtav1":
        return "6"
    if encoder_name in {"hevc_nvenc", "av1_nvenc"}:
        return "p5"
    return None


def resolve_encoder(
    codec: CodecChoice,
    backend: BackendChoice,
    available_encoders: set[str],
) -> EncoderInfo:
    cpu_map = {
        CodecChoice.HEVC: "libx265",
        CodecChoice.AV1: "libsvtav1",
    }
    nvenc_map = {
        CodecChoice.HEVC: "hevc_nvenc",
        CodecChoice.AV1: "av1_nvenc",
    }
    amf_map = {
        CodecChoice.HEVC: "hevc_amf",
        CodecChoice.AV1: "av1_amf",
    }
    backend_maps = {
        BackendChoice.CPU: cpu_map,
        BackendChoice.NVENC: nvenc_map,
        BackendChoice.AMF: amf_map,
    }

    if backend == BackendChoice.AUTO:
        for candidate_backend in (BackendChoice.NVENC, BackendChoice.AMF, BackendChoice.CPU):
            encoder_name = backend_maps[candidate_backend][codec]
            if encoder_name in available_encoders:
                return EncoderInfo(
                    codec=codec,
                    backend=candidate_backend,
                    encoder_name=encoder_name,
                    supports_two_pass=encoder_name == "libx265",
                    default_preset=default_preset_for_encoder(encoder_name),
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
        default_preset=default_preset_for_encoder(encoder_name),
    )
