from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from core.models import MediaInfo


def _run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _parse_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _guess_fps(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
        if not raw or raw in ("0/0", "N/A"):
            continue
        if "/" in str(raw):
            num, den = str(raw).split("/", 1)
            try:
                den_value = float(den)
                if den_value == 0:
                    continue
                return float(num) / den_value
            except ValueError:
                continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def ffprobe_json(ffprobe_path: Path, input_path: Path) -> dict[str, Any]:
    cmd = [
        str(ffprobe_path),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]
    proc = _run_command(cmd)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe did not return valid JSON for: {input_path}") from exc


def probe_media_info(ffprobe_path: Path, input_path: Path) -> MediaInfo:
    data = ffprobe_json(ffprobe_path, input_path)
    streams = data.get("streams", [])
    fmt = data.get("format", {}) or {}

    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not video_stream:
        raise RuntimeError(f"No video stream found in: {input_path}")

    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    first_audio = audio_streams[0] if audio_streams else None

    duration = _parse_float(fmt.get("duration")) or _parse_float(video_stream.get("duration")) or 0.0
    if duration <= 0:
        raise RuntimeError(f"Cannot determine media duration for: {input_path}")

    format_bitrate_bps = _parse_int(fmt.get("bit_rate"))
    if format_bitrate_bps <= 0:
        format_bitrate_bps = max(1, int(round(input_path.stat().st_size * 8 / duration)))

    video_bitrate_bps = _parse_int(video_stream.get("bit_rate"))
    audio_bitrate_bps = sum(_parse_int(item.get("bit_rate")) for item in audio_streams)

    if video_bitrate_bps <= 0:
        estimated = format_bitrate_bps - audio_bitrate_bps
        if estimated > 0:
            video_bitrate_bps = estimated
        else:
            video_bitrate_bps = max(300_000, int(round(format_bitrate_bps * 0.85)))

    width = video_stream.get("width")
    height = video_stream.get("height")
    fps = _guess_fps(video_stream)
    video_codec = str(video_stream.get("codec_name") or "unknown")
    audio_codec = str(first_audio.get("codec_name")) if first_audio else None

    return MediaInfo(
        path=input_path,
        duration=duration,
        format_bitrate_bps=format_bitrate_bps,
        video_bitrate_bps=video_bitrate_bps,
        audio_bitrate_bps=audio_bitrate_bps,
        width=width if isinstance(width, int) else None,
        height=height if isinstance(height, int) else None,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )
