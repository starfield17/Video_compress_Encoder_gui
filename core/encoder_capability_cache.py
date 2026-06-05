from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from core.encoder_caps import AUTO_BACKEND_PRIORITY, ENCODER_CANDIDATES, list_available_encoders
from core.models import BackendChoice, CodecChoice
from core.preset_store import load_app_config, update_app_config


ENCODER_CAPABILITIES_SCHEMA_VERSION = 2
SMOKE_TEST_SOURCE_SIZE = "256x256"
SMOKE_TEST_TIMEOUT_SEC = 10.0
_CACHE_LOCK = threading.RLock()


def _emit(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)


def _resolved_ffmpeg_path(ffmpeg_path: Path) -> Path:
    return ffmpeg_path.expanduser().resolve()


def _ffmpeg_mtime_ns(ffmpeg_path: Path) -> int:
    return int(ffmpeg_path.stat().st_mtime_ns)


def _ffmpeg_version_line(ffmpeg_path: Path) -> str:
    proc = subprocess.run(
        [str(ffmpeg_path), "-version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    for line in "\n".join([proc.stdout, proc.stderr]).splitlines():
        normalized = line.strip()
        if normalized:
            return normalized
    return ""


# Validate that the cached data structure matches the current ENCODER_CANDIDATES
# schema. A mismatch means the app was upgraded and the cache must be rebuilt.
def _valid_capability_shape(capabilities: dict) -> bool:
    codecs = capabilities.get("codecs")
    if not isinstance(codecs, dict):
        return False
    for codec in CodecChoice:
        items = codecs.get(codec.value)
        if not isinstance(items, list):
            return False
        expected = ENCODER_CANDIDATES[codec]
        for item in items:
            if not isinstance(item, dict):
                return False
            try:
                backend = BackendChoice(str(item.get("backend", "")))
            except ValueError:
                return False
            if expected.get(backend) != str(item.get("encoder", "")).strip():
                return False
    return True


def load_cached_encoder_capabilities(config_dir: Path) -> dict | None:
    data = load_app_config(config_dir)
    capabilities = data.get("encoder_capabilities")
    return capabilities if isinstance(capabilities, dict) else None


def save_encoder_capabilities(config_dir: Path, capabilities: dict) -> Path:
    return update_app_config(
        config_dir,
        lambda data: {**data, "encoder_capabilities": capabilities},
    )


def is_encoder_capability_cache_valid(capabilities: dict | None, ffmpeg_path: Path) -> bool:
    if not isinstance(capabilities, dict):
        return False
    if capabilities.get("schema_version") != ENCODER_CAPABILITIES_SCHEMA_VERSION:
        return False

    try:
        resolved = _resolved_ffmpeg_path(ffmpeg_path)
        cached_mtime = int(capabilities.get("ffmpeg_mtime_ns", -1))
        current_mtime = _ffmpeg_mtime_ns(resolved)
        current_version = _ffmpeg_version_line(resolved)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False

    if str(capabilities.get("ffmpeg_path", "")) != str(resolved):
        return False
    if cached_mtime != current_mtime:
        return False
    if str(capabilities.get("ffmpeg_version", "")) != current_version:
        return False
    return _valid_capability_shape(capabilities)


# Render a single test frame then discard it. A clean exit means the encoder
# binary is present and functional at runtime, not just listed in -encoders.
def smoke_test_encoder(
    ffmpeg_path: Path,
    encoder_name: str,
    *,
    timeout_sec: float = SMOKE_TEST_TIMEOUT_SEC,
) -> bool:
    cmd = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={SMOKE_TEST_SOURCE_SIZE}:rate=1",
        "-frames:v",
        "1",
        "-an",
        "-c:v",
        encoder_name,
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _iter_codec_candidates(codec: CodecChoice) -> Iterable[tuple[BackendChoice, str]]:
    backend_map = ENCODER_CANDIDATES[codec]
    for backend in AUTO_BACKEND_PRIORITY:
        yield backend, backend_map[backend]


def detect_encoder_capabilities(
    ffmpeg_path: Path,
    available_encoders: set[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    resolved = _resolved_ffmpeg_path(ffmpeg_path)
    if available_encoders is None:
        _emit(progress_callback, "Scanning FFmpeg encoder list...")
        available_encoders = list_available_encoders(resolved)

    capabilities: dict[str, object] = {
        "schema_version": ENCODER_CAPABILITIES_SCHEMA_VERSION,
        "ffmpeg_path": str(resolved),
        "ffmpeg_mtime_ns": _ffmpeg_mtime_ns(resolved),
        "ffmpeg_version": _ffmpeg_version_line(resolved),
        "detected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "codecs": {},
    }

    codecs: dict[str, list[dict[str, str]]] = {}
    for codec in CodecChoice:
        usable: list[dict[str, str]] = []
        for backend, encoder_name in _iter_codec_candidates(codec):
            if encoder_name not in available_encoders:
                _emit(progress_callback, f"{codec.value}/{backend.value}: {encoder_name} is not in this FFmpeg build.")
                continue
            _emit(progress_callback, f"{codec.value}/{backend.value}: testing {encoder_name}...")
            if smoke_test_encoder(resolved, encoder_name):
                usable.append({"backend": backend.value, "encoder": encoder_name})
                _emit(progress_callback, f"{codec.value}/{backend.value}: {encoder_name} is usable.")
            else:
                _emit(progress_callback, f"{codec.value}/{backend.value}: {encoder_name} failed the smoke test.")
        codecs[codec.value] = usable
    capabilities["codecs"] = codecs
    return capabilities


def ensure_encoder_capabilities(
    config_dir: Path,
    ffmpeg_path: Path,
    *,
    force_refresh: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    resolved = _resolved_ffmpeg_path(ffmpeg_path)
    with _CACHE_LOCK:
        cached = load_cached_encoder_capabilities(config_dir)
        if not force_refresh and is_encoder_capability_cache_valid(cached, resolved):
            return cached

        _emit(progress_callback, "Detecting hardware encoders...")
        capabilities = detect_encoder_capabilities(resolved, progress_callback=progress_callback)
        save_encoder_capabilities(config_dir, capabilities)
        return capabilities
