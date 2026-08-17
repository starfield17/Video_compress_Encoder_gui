from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.models import AnalysisReceipt, QualityCandidateResult


ANALYSIS_RECEIPT_SCHEMA_VERSION = 2
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
_RECEIPT_LOCK = threading.RLock()


def analysis_receipts_dir(workdir: Path) -> Path:
    return workdir / "analysis" / "receipts"


def analysis_receipt_path(workdir: Path, measurement_fingerprint: str) -> Path:
    if _FINGERPRINT_RE.fullmatch(measurement_fingerprint) is None:
        raise ValueError("Analysis receipt fingerprint must be a SHA-256 hex digest.")
    return analysis_receipts_dir(workdir) / f"{measurement_fingerprint}.json"


def _candidate_from_data(data: object) -> QualityCandidateResult:
    if not isinstance(data, dict):
        raise ValueError("Analysis receipt candidate must be an object.")
    video_bitrate_bps = int(data["video_bitrate_bps"])
    segment_vmaf = [float(value) for value in data.get("segment_vmaf", [])]
    min_vmaf = float(data.get("min_vmaf", 0.0))
    encoded_bytes = [int(value) for value in data.get("encoded_bytes", [])]
    encoded_durations_sec = [float(value) for value in data.get("encoded_durations_sec", [])]
    observed_video_bitrate_bps = int(data.get("observed_video_bitrate_bps", 0))
    predicted_output_bytes = (
        None if data.get("predicted_output_bytes") is None else int(data["predicted_output_bytes"])
    )
    predicted_output_ratio = (
        None if data.get("predicted_output_ratio") is None else float(data["predicted_output_ratio"])
    )
    if video_bitrate_bps <= 0 or observed_video_bitrate_bps < 0:
        raise ValueError("Analysis receipt contains an invalid bitrate.")
    if not math.isfinite(min_vmaf) or any(not math.isfinite(value) for value in segment_vmaf):
        raise ValueError("Analysis receipt contains a non-finite VMAF value.")
    if any(value < 0 for value in encoded_bytes):
        raise ValueError("Analysis receipt contains an invalid encoded size.")
    if any(not math.isfinite(value) or value <= 0 for value in encoded_durations_sec):
        raise ValueError("Analysis receipt contains an invalid encoded duration.")
    if len(encoded_bytes) != len(encoded_durations_sec):
        raise ValueError("Analysis receipt sample measurements have different lengths.")
    if predicted_output_bytes is not None and predicted_output_bytes < 0:
        raise ValueError("Analysis receipt contains an invalid output size prediction.")
    if predicted_output_ratio is not None and (
        not math.isfinite(predicted_output_ratio) or predicted_output_ratio < 0
    ):
        raise ValueError("Analysis receipt contains an invalid output ratio prediction.")
    return QualityCandidateResult(
        video_bitrate_bps=video_bitrate_bps,
        segment_vmaf=segment_vmaf,
        min_vmaf=min_vmaf,
        encoded_bytes=encoded_bytes,
        encoded_durations_sec=encoded_durations_sec,
        observed_video_bitrate_bps=observed_video_bitrate_bps,
        predicted_output_bytes=predicted_output_bytes,
        predicted_output_ratio=predicted_output_ratio,
    )


def _identity(data: object, field_name: str) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ValueError(f"Analysis receipt {field_name} must be an object.")
    return {str(key): value for key, value in data.items()}


def _receipt_from_data(data: object) -> AnalysisReceipt:
    if not isinstance(data, dict):
        raise ValueError("Analysis receipt must be an object.")
    schema_version = int(data.get("schema_version", 0))
    if schema_version != ANALYSIS_RECEIPT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported analysis receipt schema: {schema_version}")
    fingerprint = str(data["measurement_fingerprint"])
    if _FINGERPRINT_RE.fullmatch(fingerprint) is None:
        raise ValueError("Analysis receipt contains an invalid fingerprint.")
    raw_windows = data.get("sample_windows", [])
    if not isinstance(raw_windows, list):
        raise ValueError("Analysis receipt sample_windows must be a list.")
    windows: list[tuple[float, float]] = []
    for window in raw_windows:
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            raise ValueError("Analysis receipt sample window must contain start and duration.")
        start_sec, duration_sec = float(window[0]), float(window[1])
        if not math.isfinite(start_sec) or start_sec < 0 or not math.isfinite(duration_sec) or duration_sec <= 0:
            raise ValueError("Analysis receipt contains an invalid sample window.")
        windows.append((start_sec, duration_sec))
    raw_candidates = data.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raise ValueError("Analysis receipt candidates must be a list.")
    return AnalysisReceipt(
        schema_version=schema_version,
        measurement_fingerprint=fingerprint,
        source_identity=_identity(data.get("source_identity"), "source_identity"),
        ffmpeg_identity=_identity(data.get("ffmpeg_identity"), "ffmpeg_identity"),
        encoder_identity=_identity(data.get("encoder_identity"), "encoder_identity"),
        sample_scheme_version=int(data.get("sample_scheme_version", 0)),
        sample_windows=windows,
        candidates=[_candidate_from_data(candidate) for candidate in raw_candidates],
        created_at=str(data.get("created_at", "")),
    )


def load_analysis_receipt(workdir: Path, measurement_fingerprint: str) -> AnalysisReceipt | None:
    path = analysis_receipt_path(workdir, measurement_fingerprint)
    with _RECEIPT_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            receipt = _receipt_from_data(data)
        except (FileNotFoundError, OSError, KeyError, OverflowError, TypeError, ValueError, json.JSONDecodeError):
            return None
    if receipt.measurement_fingerprint != measurement_fingerprint:
        return None
    return receipt


def save_analysis_receipt(workdir: Path, receipt: AnalysisReceipt) -> Path:
    path = analysis_receipt_path(workdir, receipt.measurement_fingerprint)
    payload: dict[str, Any] = asdict(receipt)
    _receipt_from_data(payload)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    with _RECEIPT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(serialized)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def delete_analysis_receipt(workdir: Path, measurement_fingerprint: str) -> None:
    path = analysis_receipt_path(workdir, measurement_fingerprint)
    with _RECEIPT_LOCK:
        path.unlink(missing_ok=True)


def clear_analysis_receipts(workdir: Path) -> int:
    root = analysis_receipts_dir(workdir)
    removed = 0
    with _RECEIPT_LOCK:
        if not root.exists():
            return 0
        for path in root.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed
