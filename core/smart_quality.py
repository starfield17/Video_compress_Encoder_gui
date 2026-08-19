from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO, cast

from core.analysis_concurrency import analysis_concurrency_limit
from core.analysis_receipts import ANALYSIS_RECEIPT_SCHEMA_VERSION, load_analysis_receipt, save_analysis_receipt
from core.analysis_runtime import (
    SOURCE_DECODE_SOFTWARE,
    AnalysisDecodePolicy,
    AnalysisExecutionPlan,
    AnalysisTier,
    build_analysis_execution_plan,
    cpu_vmaf_plan,
    detect_analysis_capabilities,
    legacy_loopback_plan,
    search_tolerance_bps,
    software_source_plan,
    source_decode_args,
)
from core.build_ffmpeg_cmd import build_encode_commands, build_video_args
from core.models import (
    AnalysisProfileSettings,
    AnalysisReceipt,
    AudioMode,
    CodecChoice,
    CompressionMode,
    ConstraintFailureKind,
    ConstraintPolicy,
    ContainerChoice,
    DecisionActionCode,
    DecisionOption,
    DecodeAcceleration,
    EncodeOptions,
    EncodePlanItem,
    OperationCancelledError,
    QualityCandidateResult,
    QualitySearchResult,
    QualitySearchStatus,
    SizeBlockedPolicy,
    VmafBackend,
    VmafRuntimeSupport,
)
from core.subprocess_utils import hidden_popen_kwargs
from core.vmaf_runtime import (
    EXACT_VMAF_SUBSAMPLE,
    PTS_RESET_FILTER,
    VMAF_ASPECT_POLICY,
    VMAF_MEASUREMENT_BIT_DEPTH,
    VMAF_MEASUREMENT_PIX_FMT,
    VMAF_MODEL_GENERATION,
    VMAF_RESOLUTION_MODE,
    VMAF_SCALE_FLAGS,
    VmafEncodeMetadata,
    VmafModelSpec,
    build_cpu_vmaf_filter_graph,
    build_cpu_vmaf_command,
    build_cuda_vmaf_command,
    candidate_encode_metadata,
    parse_vmaf_json,
    select_vmaf_model,
    select_vmaf_runtime,
)


DEFAULT_MAX_OUTPUT_RATIO = {
    CodecChoice.HEVC: 0.70,
    CodecChoice.AV1: 0.50,
}
MAX_SEARCH_CANDIDATES = 8
CONTAINER_BUDGET_FACTOR = 0.98
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
SMART_ERROR_TAIL_CHARS = 4_000
SMART_SAMPLE_SCHEME_VERSION = 3
SMART_ANALYSIS_ALGORITHM_VERSION = 4
SMART_WHOLE_VIDEO_MAX_SEC = 10.0
SMART_SAMPLE_DURATION_SEC = 5.0
SMART_SAMPLE_CENTERS = (0.20, 0.50, 0.80)


class SmartCommandError(subprocess.CalledProcessError):
    """A failed Smart command with enough context to diagnose its phase.

    Smart analysis runs several FFmpeg commands for every candidate.  The
    normal ``CalledProcessError`` text only exposes an exit code, which makes
    a Windows path/filtergraph failure especially difficult to identify.  We
    retain the ``CalledProcessError`` shape for existing callers while adding
    a bounded output tail and the phase that failed.
    """

    def __init__(self, returncode: int, cmd: list[str], phase: str, output: str) -> None:
        self.phase = phase
        self.output_tail = output[-SMART_ERROR_TAIL_CHARS:]
        command = " ".join(str(part) for part in cmd)
        tail = self.output_tail.strip() or "(no command output)"
        diagnostic = (
            f"Smart {phase} failed with exit code {returncode}: {command}\n"
            f"Output tail (last {SMART_ERROR_TAIL_CHARS} characters):\n{tail}"
        )
        super().__init__(
            returncode,
            cmd,
            output=diagnostic,
            stderr=diagnostic,
        )

    def __str__(self) -> str:
        command = " ".join(str(part) for part in self.cmd)
        tail = self.output_tail.strip() or "(no command output)"
        return (
            f"Smart {self.phase} failed with exit code {self.returncode}: {command}\n"
            f"Output tail (last {SMART_ERROR_TAIL_CHARS} characters):\n{tail}"
        )


class _UnsupportedSmartAnalysis(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SampleWindow:
    start_sec: float
    duration_sec: float


@dataclass(frozen=True, slots=True)
class SmartBitrateBudget:
    source_bytes: int
    max_output_bytes: int
    audio_bitrate_bps: int
    min_video_bitrate_bps: int
    max_video_bitrate_bps: int


def resolve_max_output_ratio(codec: CodecChoice, configured: float | None) -> float:
    ratio = DEFAULT_MAX_OUTPUT_RATIO[codec] if configured is None else float(configured)
    if not 0 < ratio <= 1:
        raise ValueError("max_output_ratio must be greater than 0 and at most 1")
    return ratio


def choose_smart_sample_windows(
    duration_sec: float,
    settings: AnalysisProfileSettings | None = None,
) -> list[SampleWindow]:
    if duration_sec <= 0:
        raise ValueError("Source duration must be greater than 0.")
    profile = settings or AnalysisProfileSettings()
    if duration_sec <= profile.whole_video_max_sec:
        return [SampleWindow(0.0, duration_sec)]

    window_count = max(1, int(profile.sample_window_count))
    sample_duration = min(profile.sample_duration_sec, duration_sec / window_count)
    max_start = max(0.0, duration_sec - sample_duration)
    if window_count == 1:
        return [SampleWindow(max_start / 2.0, sample_duration)]
    fractions = tuple((index + 1) / (window_count + 1) for index in range(window_count))
    if window_count == 3:
        fractions = SMART_SAMPLE_CENTERS
    starts = [
        max(0.0, min(max_start, duration_sec * fraction - sample_duration / 2.0))
        for fraction in fractions
    ]
    if any(starts[index + 1] < starts[index] + sample_duration for index in range(len(starts) - 1)):
        if window_count == 3:
            starts = [0.0, max_start / 2.0, max_start]
        else:
            starts = [max_start * index / max(window_count - 1, 1) for index in range(window_count)]
    return [SampleWindow(start, sample_duration) for start in starts]


def parse_bitrate_bps(raw: str) -> int:
    value = raw.strip().lower()
    multiplier = 1
    if value.endswith("k"):
        value, multiplier = value[:-1], 1_000
    elif value.endswith("m"):
        value, multiplier = value[:-1], 1_000_000
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid audio bitrate: {raw}") from exc
    if parsed <= 0:
        raise ValueError("Audio bitrate must be greater than 0.")
    return int(round(parsed * multiplier))


def calculate_smart_bitrate_budget(item: EncodePlanItem) -> SmartBitrateBudget:
    if item.media_info is None:
        raise ValueError("Smart analysis requires media information.")
    source_bytes = item.source_path.stat().st_size
    ratio = resolve_max_output_ratio(item.options.codec, item.options.max_output_ratio)
    max_output_bytes = max(1, math.floor(source_bytes * ratio))
    duration = item.media_info.duration
    total_media_bitrate = math.floor(max_output_bytes * 8 * CONTAINER_BUDGET_FACTOR / duration)

    if item.options.audio_mode == AudioMode.COPY:
        audio_bitrate = max(0, int(item.media_info.audio_bitrate_bps))
    else:
        stream_count = max(0, int(item.media_info.audio_stream_count or 0))
        audio_bitrate = parse_bitrate_bps(item.options.audio_bitrate) * stream_count

    max_video_bitrate = total_media_bitrate - audio_bitrate
    configured_max = int(item.options.max_video_kbps) * 1_000
    if configured_max > 0:
        max_video_bitrate = min(max_video_bitrate, configured_max)
    return SmartBitrateBudget(
        source_bytes=source_bytes,
        max_output_bytes=max_output_bytes,
        audio_bitrate_bps=audio_bitrate,
        min_video_bitrate_bps=max(1, int(item.options.min_video_kbps) * 1_000),
        max_video_bitrate_bps=max_video_bitrate,
    )


def predicted_output_size(
    video_bitrate_bps: int,
    audio_bitrate_bps: int,
    duration_sec: float,
) -> int:
    media_bytes = (video_bitrate_bps + audio_bitrate_bps) * duration_sec / 8.0
    return int(math.ceil(media_bytes / CONTAINER_BUDGET_FACTOR))


def _floor_candidate(value: int) -> int:
    return max(1_000, int(value // 1_000) * 1_000)


def _ceil_candidate(value: int) -> int:
    return max(1_000, int(math.ceil(value / 1_000.0)) * 1_000)


def search_bitrate_candidates(
    *,
    evaluate: Callable[[int], QualityCandidateResult],
    min_bitrate_bps: int,
    budget_bitrate_bps: int,
    required_search_ceiling_bps: int,
    min_vmaf: float,
    max_candidates: int = MAX_SEARCH_CANDIDATES,
    max_output_bytes: int | None = None,
    initial_candidates: list[QualityCandidateResult] | None = None,
    tolerance_bps: int | None = None,
) -> tuple[list[QualityCandidateResult], int | None, int | None]:
    """Return tested candidates, a selectable bitrate, and required bitrate.

    When ``max_output_bytes`` is supplied, a candidate is selectable only when
    both its VMAF and its measured output estimate satisfy the constraints.
    ``required_bitrate`` intentionally remains the lowest *quality-passing*
    candidate, even when that candidate is too large.  The caller can then
    report the measured ratio that explains why the joint constraints are
    unsatisfiable.
    """
    cache: dict[int, QualityCandidateResult] = {
        candidate.video_bitrate_bps: candidate for candidate in (initial_candidates or [])
    }
    candidate_limit = min(MAX_SEARCH_CANDIDATES, max(0, int(max_candidates)))
    stop_delta = max(1_000, int(tolerance_bps) if tolerance_bps is not None else 1_000)
    evaluated = 0

    def quality_passes(result: QualityCandidateResult) -> bool:
        return result.min_vmaf >= min_vmaf

    def constraints_pass(result: QualityCandidateResult) -> bool:
        if not quality_passes(result):
            return False
        if max_output_bytes is None:
            return True
        predicted_bytes = result.predicted_output_bytes
        return predicted_bytes is not None and predicted_bytes <= max_output_bytes

    def summarize(*, allow_selected: bool) -> tuple[int | None, int | None]:
        quality_passing = [result.video_bitrate_bps for result in cache.values() if quality_passes(result)]
        required = min(quality_passing) if quality_passing else None
        if not allow_selected:
            return None, required
        selectable = [result.video_bitrate_bps for result in cache.values() if constraints_pass(result)]
        return (min(selectable) if selectable else None), required

    def test(value: int) -> QualityCandidateResult | None:
        nonlocal evaluated
        bitrate = _floor_candidate(value)
        if bitrate in cache:
            return cache.get(bitrate)
        if evaluated >= candidate_limit:
            return None
        cache[bitrate] = evaluate(bitrate)
        evaluated += 1
        return cache[bitrate]

    budget = _floor_candidate(budget_bitrate_bps)
    minimum = _ceil_candidate(min_bitrate_bps)
    upper_score = test(budget)
    if upper_score is None:
        return list(cache.values()), None, None

    if quality_passes(upper_score):
        low_score = test(minimum)
        if low_score is not None and quality_passes(low_score):
            selected, required = summarize(allow_selected=True)
            return list(cache.values()), selected, required
        low = minimum
        high = budget
        while evaluated < candidate_limit and high - low > stop_delta:
            middle = _floor_candidate((low + high) // 2)
            if middle in cache:
                break
            score = test(middle)
            if score is None:
                break
            if quality_passes(score):
                high = middle
            else:
                low = middle
        selected, required = summarize(allow_selected=True)
        return list(cache.values()), selected, required

    ceiling = _floor_candidate(max(required_search_ceiling_bps, budget))
    ceiling_score = test(ceiling) if ceiling > budget else upper_score
    if ceiling_score is None or not quality_passes(ceiling_score):
        return list(cache.values()), None, None

    low = budget
    high = ceiling
    while evaluated < candidate_limit and high - low > stop_delta:
        middle = _floor_candidate((low + high) // 2)
        if middle in cache:
            break
        score = test(middle)
        if score is None:
            break
        if quality_passes(score):
            high = middle
        else:
            low = middle
    # Without a size constraint, preserve the historical behavior: a quality
    # target requiring more than the initial budget is reported as required,
    # never selected.  Smart analysis enables selection for this branch only
    # when the candidate's measured output also proves it fits the limit.
    selected, required = summarize(allow_selected=max_output_bytes is not None)
    return list(cache.values()), selected, required


def _path_identity(path: Path) -> dict[str, object]:
    try:
        stat = path.stat()
        return {
            "path": str(path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        return {"path": str(path), "size": None, "mtime_ns": None}


def measurement_configuration_payload(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    *,
    vmaf_backend: VmafBackend = VmafBackend.CPU,
    vmaf_subsample: int = EXACT_VMAF_SUBSAMPLE,
) -> dict[str, object]:
    if item.encoder_info is None:
        raise ValueError("Smart analysis requires a bound encoder.")
    if item.media_info is None:
        raise ValueError("Smart analysis requires probed media.")
    options = item.options
    media = item.media_info
    model_spec = select_vmaf_model(media)
    encode_metadata = candidate_encode_metadata(media, options.pix_fmt)
    return {
        "source": _path_identity(item.source_path),
        "ffmpeg": _path_identity(ffmpeg_path),
        "codec": options.codec.value,
        "encoder": item.encoder_info.encoder_name,
        "backend": item.encoder_info.backend.value,
        "preset": options.encoder_preset,
        "pix_fmt": options.pix_fmt,
        "two_pass": options.two_pass,
        "maxrate_factor": options.maxrate_factor,
        "bufsize_factor": options.bufsize_factor,
        "sample_scheme_version": SMART_SAMPLE_SCHEME_VERSION,
        "whole_video_max_sec": options.analysis_settings.whole_video_max_sec,
        "sample_duration_sec": options.analysis_settings.sample_duration_sec,
        "sample_window_count": options.analysis_settings.sample_window_count,
        "source_width": media.width,
        "source_height": media.height,
        "source_fps": media.fps,
        "source_pix_fmt": media.pix_fmt,
        "source_bit_depth": media.bit_depth,
        "candidate_encode_width": encode_metadata.width,
        "candidate_encode_height": encode_metadata.height,
        "candidate_encode_bit_depth": encode_metadata.bit_depth,
        "vmaf_generation": VMAF_MODEL_GENERATION,
        "vmaf_resolution_mode": VMAF_RESOLUTION_MODE,
        "vmaf_pooling": "lowest_sampled_window_mean",
        "vmaf_model": model_spec.name,
        "vmaf_hfr": model_spec.hfr,
        "vmaf_display_width": model_spec.display_width,
        "vmaf_display_height": model_spec.display_height,
        "vmaf_measurement_pix_fmt": VMAF_MEASUREMENT_PIX_FMT,
        "vmaf_measurement_bit_depth": VMAF_MEASUREMENT_BIT_DEPTH,
        "vmaf_scale_algorithm": VMAF_SCALE_FLAGS,
        "vmaf_aspect_policy": VMAF_ASPECT_POLICY,
        "vmaf_subsample": int(vmaf_subsample),
        "vmaf_backend": vmaf_backend.value,
        "analysis_algorithm_version": SMART_ANALYSIS_ALGORITHM_VERSION,
    }


def measurement_configuration_fingerprint(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    *,
    vmaf_backend: VmafBackend = VmafBackend.CPU,
    vmaf_subsample: int = EXACT_VMAF_SUBSAMPLE,
) -> str:
    payload = measurement_configuration_payload(
        ffmpeg_path,
        item,
        vmaf_backend=vmaf_backend,
        vmaf_subsample=vmaf_subsample,
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def quality_configuration_fingerprint(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    *,
    vmaf_backend: VmafBackend = VmafBackend.CPU,
    vmaf_subsample: int = EXACT_VMAF_SUBSAMPLE,
    measurement_fingerprint: str | None = None,
) -> str:
    options = item.options
    measurement_key = measurement_fingerprint or measurement_configuration_fingerprint(
        ffmpeg_path,
        item,
        vmaf_backend=vmaf_backend,
        vmaf_subsample=vmaf_subsample,
    )
    settings = options.analysis_settings
    payload = {
        "measurement_fingerprint": measurement_key,
        "min_vmaf": options.min_vmaf,
        "max_output_ratio": resolve_max_output_ratio(options.codec, options.max_output_ratio),
        "audio_mode": options.audio_mode.value,
        "audio_bitrate": options.audio_bitrate,
        "min_video_kbps": options.min_video_kbps,
        "max_video_kbps": options.max_video_kbps,
        "container": options.container.value,
        "coarse_max_candidates": settings.coarse_max_candidates,
        "exact_max_candidates": settings.exact_max_candidates,
        "coarse_vmaf_subsample": settings.coarse_vmaf_subsample,
        "exact_vmaf_subsample": settings.exact_vmaf_subsample,
        "min_search_tolerance_bps": settings.min_search_tolerance_bps,
        "search_tolerance_ratio": settings.search_tolerance_ratio,
        "analysis_algorithm_version": SMART_ANALYSIS_ALGORITHM_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unsupported_reason(item: EncodePlanItem, support: VmafRuntimeSupport | None = None) -> str | None:
    media = item.media_info
    if media and media.color_transfer and media.color_transfer.lower() in HDR_TRANSFERS:
        return f"HDR transfer {media.color_transfer!r} is not supported by smart mode."
    if support is not None and not support.runnable:
        return support.error_message or f"VMAF model {support.model} is unavailable on {support.backend.value}."
    return None


def _run_logged(
    cmd: list[str],
    log_file: TextIO,
    *,
    cancel_check: Callable[[], bool] | None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None,
    cwd: Path | None = None,
    phase: str = "command",
) -> None:
    log_file.write("$ " + " ".join(cmd) + "\n")
    log_file.flush()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=cwd,
            **hidden_popen_kwargs(),
        )
    except OSError as exc:
        os_error = str(exc)[:SMART_ERROR_TAIL_CHARS]
        log_file.write(
            f"[smart process start failed] phase={phase}\n"
            f"OS error: {os_error}\n"
        )
        log_file.flush()
        command = " ".join(str(part) for part in cmd)
        raise RuntimeError(
            f"Smart {phase} failed to start (exit code unavailable): {command}\n"
            f"Output tail: {os_error}"
        ) from exc
    if process_callback is not None:
        process_callback(proc)
    output_tail = ""
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            output_tail = (output_tail + line)[-SMART_ERROR_TAIL_CHARS:]
            log_file.write(line)
            if cancel_check is not None and cancel_check():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise OperationCancelledError("Smart analysis cancelled.")
        return_code = proc.wait()
    finally:
        if process_callback is not None:
            process_callback(None)
    if return_code != 0:
        failure = SmartCommandError(return_code, cmd, phase, output_tail)
        log_file.write(
            f"[smart command failed] phase={phase} exit_code={return_code}\n"
            f"{failure.output_tail}\n"
        )
        log_file.flush()
        raise failure


def _log_timing(log_file: TextIO, message: str) -> None:
    log_file.write(f"[smart timing] {message}\n")
    log_file.flush()


def _build_reference(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    window: SampleWindow,
    output_path: Path,
    *,
    decode_acceleration: str = SOURCE_DECODE_SOFTWARE,
) -> list[str]:
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-y",
        *source_decode_args(decode_acceleration),
        "-ss",
        f"{window.start_sec:.3f}",
        "-t",
        f"{window.duration_sec:.3f}",
        "-i",
        str(item.source_path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        PTS_RESET_FILTER,
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-g",
        "1",
        str(output_path),
    ]


def _loopback_decoder_name(encoder_name: str) -> str:
    lowered = encoder_name.lower()
    if "av1" in lowered:
        return "av1"
    if "hevc" in lowered or "x265" in lowered:
        return "hevc"
    if "h264" in lowered or "x264" in lowered:
        return "h264"
    return "hevc"


def _build_loopback_score_command(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    window: SampleWindow,
    output_path: Path,
    plan: AnalysisExecutionPlan,
    *,
    model_spec: VmafModelSpec,
    encode_metadata: VmafEncodeMetadata,
    log_name: str,
) -> list[str]:
    if plan.vmaf_backend != VmafBackend.CPU:
        raise ValueError("VMAF v1 loopback scoring currently supports only the CPU backend.")
    video_args = build_video_args(item, extra_args=plan.encoder_extra_args)
    filter_graph = build_cpu_vmaf_filter_graph(
        model_spec=model_spec,
        encode_metadata=encode_metadata,
        log_path=log_name,
        n_threads=plan.vmaf_threads,
        n_subsample=plan.vmaf_subsample,
        distorted_input="dec:v",
        reference_input="0:v",
    )
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-y",
        *source_decode_args(plan.source_decode_acceleration),
        "-ss",
        f"{window.start_sec:.3f}",
        "-t",
        f"{window.duration_sec:.3f}",
        "-i",
        str(item.source_path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        *video_args,
        "-dec:v",
        _loopback_decoder_name(plan.encoder_name),
        str(output_path),
        "-filter_complex",
        filter_graph,
        "-an",
        "-f",
        "null",
        "-",
    ]


def _bind_candidate_item(item: EncodePlanItem, bitrate_bps: int, plan: AnalysisExecutionPlan) -> EncodePlanItem:
    encoder_info = item.encoder_info
    if encoder_info is not None and encoder_info.encoder_name != plan.encoder_name:
        encoder_info = replace(encoder_info, encoder_name=plan.encoder_name)
    return replace(
        item,
        encoder_info=encoder_info,
        options=replace(
            item.options,
            decode_acceleration=DecodeAcceleration.SOFTWARE,
            container=ContainerChoice.MKV,
            copy_subtitles=False,
            copy_external_subtitles=False,
            overwrite=True,
            encoder_preset=plan.encoder_preset,
            two_pass=plan.two_pass,
        ),
        target_video_bitrate_bps=bitrate_bps,
    )


def _vmaf_command(
    ffmpeg_path: Path,
    plan: AnalysisExecutionPlan,
    *,
    candidate_path: Path,
    reference_path: Path,
    model_spec: VmafModelSpec,
    encode_metadata: VmafEncodeMetadata,
    log_name: str,
) -> list[str]:
    if plan.vmaf_backend == VmafBackend.CUDA:
        return build_cuda_vmaf_command(
            ffmpeg_path,
            distorted_path=candidate_path,
            reference_path=reference_path,
            model_spec=model_spec,
            encode_metadata=encode_metadata,
            log_name=log_name,
            n_threads=plan.vmaf_threads,
            n_subsample=plan.vmaf_subsample,
        )
    return build_cpu_vmaf_command(
        ffmpeg_path,
        distorted_path=candidate_path,
        reference_path=reference_path,
        model_spec=model_spec,
        encode_metadata=encode_metadata,
        log_name=log_name,
        n_threads=plan.vmaf_threads,
        n_subsample=plan.vmaf_subsample,
    )


def _candidate_result(
    item: EncodePlanItem,
    bitrate_bps: int,
    scores: list[float],
    encoded_bytes: list[int],
    encoded_durations_sec: list[float],
    audio_bitrate_bps: int,
    source_bytes: int | None,
) -> QualityCandidateResult:
    observed_video_bitrate_bps = max(
        int(math.ceil(encoded_size * 8.0 / duration))
        for encoded_size, duration in zip(encoded_bytes, encoded_durations_sec)
    )
    predicted_bytes = predicted_output_size(
        observed_video_bitrate_bps,
        audio_bitrate_bps,
        item.media_info.duration if item.media_info is not None else 0.0,
    )
    predicted_ratio = None
    if source_bytes is not None and source_bytes > 0:
        predicted_ratio = predicted_bytes / source_bytes
    return QualityCandidateResult(
        video_bitrate_bps=bitrate_bps,
        segment_vmaf=scores,
        min_vmaf=min(scores),
        encoded_bytes=encoded_bytes,
        encoded_durations_sec=encoded_durations_sec,
        observed_video_bitrate_bps=observed_video_bitrate_bps,
        predicted_output_bytes=predicted_bytes,
        predicted_output_ratio=predicted_ratio,
    )


def _score_candidate_loopback(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    windows: list[SampleWindow],
    bitrate_bps: int,
    temp_root: Path,
    log_file: TextIO,
    plan: AnalysisExecutionPlan,
    *,
    cancel_check: Callable[[], bool] | None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None,
    audio_bitrate_bps: int = 0,
    source_bytes: int | None = None,
    min_vmaf_target: float | None = None,
    window_order: list[int] | None = None,
) -> QualityCandidateResult:
    candidate_item = _bind_candidate_item(item, bitrate_bps, plan)
    if item.media_info is None:
        raise ValueError("Smart analysis requires probed media.")
    model_spec = select_vmaf_model(item.media_info)
    encode_metadata = candidate_encode_metadata(item.media_info, item.options.pix_fmt)
    order = window_order or list(range(len(windows)))
    scores: dict[int, float] = {}
    encoded_bytes: dict[int, int] = {}
    encoded_durations: dict[int, float] = {}
    for window_index in order:
        window = windows[window_index]
        candidate_path = temp_root / f"loopback-{plan.tier.value}-{bitrate_bps}-{window_index}.mkv"
        json_path = temp_root / f"vmaf-{plan.tier.value}-{bitrate_bps}-{window_index}.json"
        started = time.perf_counter()
        _run_logged(
            _build_loopback_score_command(
                ffmpeg_path,
                candidate_item,
                window,
                candidate_path,
                plan,
                model_spec=model_spec,
                encode_metadata=encode_metadata,
                log_name=json_path.name,
            ),
            log_file,
            cancel_check=cancel_check,
            process_callback=process_callback,
            cwd=temp_root,
            phase="loopback score",
        )
        elapsed = time.perf_counter() - started
        _log_timing(
            log_file,
            f"{plan.tier.value} candidate {bitrate_bps} window {window_index + 1} loopback={elapsed:.2f}s",
        )
        try:
            encoded_size = candidate_path.stat().st_size
        except OSError as exc:
            raise RuntimeError(
                f"Smart loopback encode did not produce its sample output: {candidate_path}"
            ) from exc
        if window.duration_sec <= 0:
            raise RuntimeError(f"Smart candidate encode produced an invalid sample duration: {window.duration_sec}")
        score = parse_vmaf_json(json_path)
        scores[window_index] = score
        encoded_bytes[window_index] = encoded_size
        encoded_durations[window_index] = float(window.duration_sec)
        if min_vmaf_target is not None and score < min_vmaf_target:
            _log_timing(
                log_file,
                f"{plan.tier.value} candidate {bitrate_bps}: window {window_index + 1} VMAF={score:.3f} early rejected",
            )
            break
    measured = sorted(scores)
    return _candidate_result(
        item,
        bitrate_bps,
        [scores[index] for index in measured],
        [encoded_bytes[index] for index in measured],
        [encoded_durations[index] for index in measured],
        audio_bitrate_bps,
        source_bytes,
    )


def _score_candidate(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    references: list[Path],
    bitrate_bps: int,
    temp_root: Path,
    workdir: Path,
    log_file: TextIO,
    *,
    cancel_check: Callable[[], bool] | None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None,
    window_durations_sec: list[float] | None = None,
    audio_bitrate_bps: int = 0,
    source_bytes: int | None = None,
    plan: AnalysisExecutionPlan | None = None,
    min_vmaf_target: float | None = None,
    window_order: list[int] | None = None,
) -> QualityCandidateResult:
    if plan is None:
        if item.encoder_info is None:
            raise ValueError("Smart analysis requires a bound encoder.")
        plan = AnalysisExecutionPlan(
            tier=AnalysisTier.EXACT,
            source_decode_acceleration=SOURCE_DECODE_SOFTWARE,
            encoder_name=item.encoder_info.encoder_name,
            encoder_preset=item.options.encoder_preset,
            encoder_extra_args=(),
            two_pass=bool(item.options.two_pass and item.encoder_info.supports_two_pass),
            vmaf_backend=VmafBackend.CPU,
            vmaf_threads=1,
            vmaf_subsample=EXACT_VMAF_SUBSAMPLE,
            use_loopback=False,
        )
    scores: dict[int, float] = {}
    encoded_bytes: dict[int, int] = {}
    encoded_durations: dict[int, float] = {}
    candidate_item = _bind_candidate_item(item, bitrate_bps, plan)
    if item.media_info is None:
        raise ValueError("Smart analysis requires probed media.")
    model_spec = select_vmaf_model(item.media_info)
    encode_metadata = candidate_encode_metadata(item.media_info, item.options.pix_fmt)
    order = window_order or list(range(len(references)))
    for window_index in order:
        reference = references[window_index]
        candidate_path = temp_root / f"candidate-{plan.tier.value}-{bitrate_bps}-{window_index}.mkv"
        commands, passlog = build_encode_commands(
            ffmpeg_path,
            candidate_item,
            workdir,
            input_path=reference,
            output_path=candidate_path,
            stage=f"smart-{plan.tier.value}-{bitrate_bps}-{window_index}",
            extra_video_args=plan.encoder_extra_args,
        )
        encode_started = time.perf_counter()
        try:
            for command in commands:
                _run_logged(
                    command,
                    log_file,
                    cancel_check=cancel_check,
                    process_callback=process_callback,
                    phase="candidate encode",
                )
        finally:
            if passlog:
                for leftover in passlog.parent.glob(passlog.name + "*"):
                    try:
                        leftover.unlink()
                    except OSError:
                        pass
        encode_elapsed = time.perf_counter() - encode_started
        try:
            encoded_size = candidate_path.stat().st_size
        except OSError as exc:
            raise RuntimeError(
                f"Smart candidate encode did not produce its sample output: {candidate_path}"
            ) from exc
        duration = (
            window_durations_sec[window_index]
            if window_durations_sec is not None and window_index < len(window_durations_sec)
            else (item.media_info.duration if item.media_info is not None else 0.0)
        )
        if duration <= 0:
            raise RuntimeError(f"Smart candidate encode produced an invalid sample duration: {duration}")
        json_path = temp_root / f"vmaf-{plan.tier.value}-{bitrate_bps}-{window_index}.json"
        json_name = json_path.name
        score_command = _vmaf_command(
            ffmpeg_path,
            plan,
            candidate_path=candidate_path,
            reference_path=reference,
            model_spec=model_spec,
            encode_metadata=encode_metadata,
            log_name=json_name,
        )
        vmaf_started = time.perf_counter()
        _run_logged(
            score_command,
            log_file,
            cancel_check=cancel_check,
            process_callback=process_callback,
            cwd=temp_root,
            phase="VMAF scoring",
        )
        vmaf_elapsed = time.perf_counter() - vmaf_started
        score = parse_vmaf_json(json_path)
        scores[window_index] = score
        encoded_bytes[window_index] = encoded_size
        encoded_durations[window_index] = float(duration)
        _log_timing(
            log_file,
            (
                f"{plan.tier.value} candidate {bitrate_bps} window {window_index + 1}: "
                f"encode={encode_elapsed:.2f}s vmaf={vmaf_elapsed:.2f}s VMAF={score:.3f}"
            ),
        )
        if min_vmaf_target is not None and score < min_vmaf_target:
            _log_timing(
                log_file,
                f"{plan.tier.value} candidate {bitrate_bps}: window {window_index + 1} VMAF={score:.3f} early rejected",
            )
            break

    measured = sorted(scores)
    return _candidate_result(
        item,
        bitrate_bps,
        [scores[index] for index in measured],
        [encoded_bytes[index] for index in measured],
        [encoded_durations[index] for index in measured],
        audio_bitrate_bps,
        source_bytes,
    )


def _refresh_candidate_predictions(
    candidates: list[QualityCandidateResult],
    budget: SmartBitrateBudget,
    duration_sec: float,
) -> list[QualityCandidateResult]:
    refreshed: list[QualityCandidateResult] = []
    for candidate in candidates:
        observed_bitrate = candidate.observed_video_bitrate_bps
        if observed_bitrate <= 0 and candidate.encoded_bytes and candidate.encoded_durations_sec:
            measured_bitrates = [
                int(math.ceil(encoded_bytes * 8.0 / duration))
                for encoded_bytes, duration in zip(candidate.encoded_bytes, candidate.encoded_durations_sec)
                if duration > 0
            ]
            if measured_bitrates:
                observed_bitrate = max(measured_bitrates)
        if observed_bitrate <= 0:
            observed_bitrate = candidate.video_bitrate_bps
        predicted_bytes = predicted_output_size(observed_bitrate, budget.audio_bitrate_bps, duration_sec)
        refreshed.append(
            replace(
                candidate,
                observed_video_bitrate_bps=observed_bitrate,
                predicted_output_bytes=predicted_bytes,
                predicted_output_ratio=(predicted_bytes / budget.source_bytes if budget.source_bytes else None),
            )
        )
    return refreshed


def reselect_from_candidates(
    candidates: list[QualityCandidateResult],
    item: EncodePlanItem,
    *,
    measurement_fingerprint: str = "",
    fingerprint: str = "",
) -> QualitySearchResult:
    if item.media_info is None or item.encoder_info is None:
        raise ValueError("Smart candidate selection requires probed media and a bound encoder.")
    budget = calculate_smart_bitrate_budget(item)
    base = {
        "encoder_name": item.encoder_info.encoder_name,
        "backend": item.encoder_info.backend,
        "measurement_fingerprint": measurement_fingerprint,
        "fingerprint": fingerprint,
        "max_output_bytes": budget.max_output_bytes,
    }
    if budget.max_video_bitrate_bps < budget.min_video_bitrate_bps:
        return QualitySearchResult(
            status=QualitySearchStatus.CONSTRAINT_UNSATISFIED,
            failure_kind=ConstraintFailureKind.MEDIA_BUDGET_TOO_SMALL,
            reason="Audio and container overhead leave too little room for the minimum video bitrate.",
            **base,
        )

    refreshed = _refresh_candidate_predictions(candidates, budget, item.media_info.duration)
    configured_max_bps = max(0, int(item.options.max_video_kbps)) * 1_000
    rate_eligible = [
        candidate
        for candidate in refreshed
        if candidate.video_bitrate_bps >= budget.min_video_bitrate_bps
        and (configured_max_bps == 0 or candidate.video_bitrate_bps <= configured_max_bps)
    ]
    quality_passing = [
        candidate for candidate in rate_eligible if candidate.min_vmaf >= item.options.min_vmaf
    ]
    selectable = [
        candidate
        for candidate in quality_passing
        if candidate.predicted_output_bytes is not None and candidate.predicted_output_bytes <= budget.max_output_bytes
    ]
    size_fitting = [
        candidate
        for candidate in rate_eligible
        if candidate.predicted_output_bytes is not None and candidate.predicted_output_bytes <= budget.max_output_bytes
    ]
    best_size_fitting = max(size_fitting, key=lambda candidate: (candidate.min_vmaf, -candidate.video_bitrate_bps), default=None)
    if selectable:
        chosen = min(selectable, key=lambda candidate: candidate.video_bitrate_bps)
        return QualitySearchResult(
            status=QualitySearchStatus.FOUND,
            candidates=refreshed,
            selected_video_bitrate_bps=chosen.video_bitrate_bps,
            min_vmaf=chosen.min_vmaf,
            predicted_output_bytes=chosen.predicted_output_bytes,
            predicted_output_ratio=chosen.predicted_output_ratio,
            best_size_fitting_candidate_bps=(best_size_fitting.video_bitrate_bps if best_size_fitting else 0),
            best_size_fitting_vmaf=(best_size_fitting.min_vmaf if best_size_fitting else None),
            **base,
        )

    required = min(quality_passing, key=lambda candidate: candidate.video_bitrate_bps, default=None)
    required_ratio = required.predicted_output_ratio if required is not None else None
    size_blocked = (
        required is not None
        and required.predicted_output_bytes is not None
        and required.predicted_output_bytes > budget.max_output_bytes
    )
    failure_kind = (
        ConstraintFailureKind.SIZE_BLOCKED if size_blocked else ConstraintFailureKind.QUALITY_UNREACHABLE
    )
    if size_blocked and required_ratio is not None:
        reason = (
            f"VMAF {item.options.min_vmaf:.1f} requires an estimated output ratio of "
            f"{required_ratio:.3f}, above the configured size limit."
        )
    else:
        reason = f"The bound encoder cannot reach VMAF {item.options.min_vmaf:.1f} with the tested candidates."
    return QualitySearchResult(
        status=QualitySearchStatus.CONSTRAINT_UNSATISFIED,
        candidates=refreshed,
        selected_video_bitrate_bps=(
            required.video_bitrate_bps
            if required is not None
            else (best_size_fitting.video_bitrate_bps if best_size_fitting is not None else 0)
        ),
        min_vmaf=(required.min_vmaf if required is not None else (best_size_fitting.min_vmaf if best_size_fitting else None)),
        predicted_output_bytes=(
            required.predicted_output_bytes
            if required is not None
            else (best_size_fitting.predicted_output_bytes if best_size_fitting is not None else None)
        ),
        predicted_output_ratio=(
            required.predicted_output_ratio
            if required is not None
            else (best_size_fitting.predicted_output_ratio if best_size_fitting is not None else None)
        ),
        required_output_ratio=required_ratio,
        required_video_bitrate_bps=(required.video_bitrate_bps if required else 0),
        best_size_fitting_candidate_bps=(best_size_fitting.video_bitrate_bps if best_size_fitting else 0),
        best_size_fitting_vmaf=(best_size_fitting.min_vmaf if best_size_fitting else None),
        failure_kind=failure_kind,
        reason=reason,
        **base,
    )


def build_decision_options(result: QualitySearchResult) -> list[DecisionOption]:
    options: list[DecisionOption] = []
    if (
        result.failure_kind == ConstraintFailureKind.SIZE_BLOCKED
        and result.required_output_ratio is not None
        and result.required_output_ratio <= 1.0
    ):
        options.append(
            DecisionOption(
                action_code=DecisionActionCode.RELAX_SIZE,
                suggested_value=math.nextafter(result.required_output_ratio, 1.0),
                requires_analysis=False,
            )
        )
    if result.best_size_fitting_vmaf is not None:
        options.append(
            DecisionOption(
                action_code=DecisionActionCode.RELAX_QUALITY,
                suggested_value=result.best_size_fitting_vmaf,
                requires_analysis=False,
            )
        )
    if result.failure_kind == ConstraintFailureKind.MEDIA_BUDGET_TOO_SMALL:
        options.append(
            DecisionOption(
                action_code=DecisionActionCode.CHANGE_MEDIA_BUDGET,
                suggested_value=AudioMode.AAC.value,
                requires_analysis=True,
            )
        )
    if result.failure_kind == ConstraintFailureKind.QUALITY_UNREACHABLE:
        options.append(
            DecisionOption(
                action_code=DecisionActionCode.REANALYZE,
                requires_analysis=True,
                parameters={"change_encoder": True},
            )
        )
    options.append(DecisionOption(action_code=DecisionActionCode.SKIP))
    return options


def constraint_policy_from_size_blocked(policy: SizeBlockedPolicy) -> ConstraintPolicy:
    if policy == SizeBlockedPolicy.RELAX_SIZE:
        return ConstraintPolicy.RELAX_SIZE
    if policy == SizeBlockedPolicy.RELAX_QUALITY:
        return ConstraintPolicy.RELAX_QUALITY
    return ConstraintPolicy.FAIL


def size_blocked_from_constraint_policy(policy: ConstraintPolicy) -> SizeBlockedPolicy:
    if policy == ConstraintPolicy.RELAX_SIZE:
        return SizeBlockedPolicy.RELAX_SIZE
    if policy == ConstraintPolicy.RELAX_QUALITY:
        return SizeBlockedPolicy.RELAX_QUALITY
    return SizeBlockedPolicy.ASK


def apply_decision_to_options(options: EncodeOptions, decision: DecisionOption) -> EncodeOptions:
    if decision.action_code == DecisionActionCode.RELAX_SIZE:
        if not isinstance(decision.suggested_value, (int, float)):
            raise ValueError("Relax-size decision requires an output ratio.")
        return replace(options, max_output_ratio=float(decision.suggested_value))
    if decision.action_code == DecisionActionCode.RELAX_QUALITY:
        if not isinstance(decision.suggested_value, (int, float)):
            raise ValueError("Relax-quality decision requires a VMAF value.")
        return replace(options, min_vmaf=float(decision.suggested_value))
    if decision.action_code == DecisionActionCode.CHANGE_MEDIA_BUDGET:
        return replace(options, audio_mode=AudioMode.AAC)
    return options


def _analysis_receipt(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    measurement_fingerprint: str,
    windows: list[SampleWindow],
    candidates: list[QualityCandidateResult],
    *,
    vmaf_backend: VmafBackend,
    vmaf_subsample: int,
    search_fingerprint: str,
) -> AnalysisReceipt:
    if item.encoder_info is None:
        raise ValueError("Smart analysis receipt requires a bound encoder.")
    payload = measurement_configuration_payload(
        ffmpeg_path,
        item,
        vmaf_backend=vmaf_backend,
        vmaf_subsample=vmaf_subsample,
    )
    return AnalysisReceipt(
        schema_version=ANALYSIS_RECEIPT_SCHEMA_VERSION,
        measurement_fingerprint=measurement_fingerprint,
        source_identity=dict(cast(dict[str, object], payload["source"])),
        ffmpeg_identity=dict(cast(dict[str, object], payload["ffmpeg"])),
        encoder_identity={
            "codec": item.options.codec.value,
            "backend": item.encoder_info.backend.value,
            "encoder": item.encoder_info.encoder_name,
            "preset": item.options.encoder_preset,
        },
        sample_scheme_version=SMART_SAMPLE_SCHEME_VERSION,
        sample_windows=[(window.start_sec, window.duration_sec) for window in windows],
        search_fingerprint=search_fingerprint,
        measurement_configuration={
            key: value for key, value in payload.items() if key not in {"source", "ffmpeg"}
        },
        candidates=candidates,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _exact_search_bounds(
    coarse_candidates: list[QualityCandidateResult],
    *,
    min_bitrate_bps: int,
    budget_bitrate_bps: int,
    ceiling_bps: int,
    min_vmaf: float,
) -> tuple[int, int, int]:
    passing = [candidate.video_bitrate_bps for candidate in coarse_candidates if candidate.min_vmaf >= min_vmaf]
    failing = [candidate.video_bitrate_bps for candidate in coarse_candidates if candidate.min_vmaf < min_vmaf]
    lower = max(failing) if failing else min_bitrate_bps
    if passing:
        seed = min(passing)
        return max(min_bitrate_bps, lower), seed, max(seed, ceiling_bps)
    return min_bitrate_bps, budget_bitrate_bps, ceiling_bps


def _complete_candidates(
    candidates: list[QualityCandidateResult],
    window_count: int,
) -> list[QualityCandidateResult]:
    return [
        candidate
        for candidate in candidates
        if len(candidate.segment_vmaf) == window_count
        and math.isclose(
            candidate.min_vmaf,
            min(candidate.segment_vmaf),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ]


def _hardest_window_index(candidate: QualityCandidateResult) -> int:
    if not candidate.segment_vmaf:
        return 0
    return min(range(len(candidate.segment_vmaf)), key=lambda index: candidate.segment_vmaf[index])


def _window_order(window_count: int, hardest_index: int) -> list[int]:
    if window_count <= 1:
        return [0] if window_count == 1 else []
    hardest = min(max(0, hardest_index), window_count - 1)
    return [hardest, *[index for index in range(window_count) if index != hardest]]


def _write_analysis_header(
    log_file: TextIO,
    item: EncodePlanItem,
    windows: list[SampleWindow],
    plan: AnalysisExecutionPlan,
) -> None:
    if item.media_info is None:
        raise ValueError("Smart analysis requires probed media.")
    model_spec = select_vmaf_model(item.media_info)
    metadata = candidate_encode_metadata(item.media_info, item.options.pix_fmt)
    sample_label = (
        f"{len(windows)}x{windows[0].duration_sec:.0f}s" if windows else "0"
    )
    log_file.write(
        "Smart analysis:\n"
        f"source={item.source_path.name}\n"
        f"profile={item.options.analysis_profile.value}\n"
        f"tier={plan.tier.value}\n"
        f"vmaf_generation={model_spec.generation}\n"
        f"vmaf_model={model_spec.name}\n"
        f"vmaf_hfr={'yes' if model_spec.hfr else 'no'}\n"
        f"vmaf_display={model_spec.display_width}x{model_spec.display_height}\n"
        f"vmaf_measurement={VMAF_MEASUREMENT_PIX_FMT}/{VMAF_MEASUREMENT_BIT_DEPTH}-bit\n"
        f"candidate_encode={metadata.width}x{metadata.height}/{metadata.bit_depth}-bit\n"
        f"source_geometry={item.media_info.width}x{item.media_info.height}\n"
        f"source_bit_depth={item.media_info.bit_depth}\n"
        "pooling=lowest_sampled_window_mean\n"
        f"hardware={plan.analysis_backend}\n"
        f"decode={plan.source_decode_acceleration}\n"
        f"candidate_encoder={plan.encoder_name}\n"
        f"vmaf={plan.vmaf_backend.value}\n"
        f"n_threads={plan.vmaf_threads}\n"
        f"n_subsample={plan.vmaf_subsample}\n"
        f"samples={sample_label}\n"
    )
    log_file.flush()


def analyze_quality(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    workdir: Path,
    log_path: Path,
    *,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> QualitySearchResult:
    if item.options.compression_mode != CompressionMode.SMART:
        raise ValueError("Quality analysis is only available in smart mode.")
    if item.media_info is None or item.encoder_info is None:
        raise ValueError("Smart analysis requires probed media and a bound encoder.")

    unsupported = _unsupported_reason(item)
    if unsupported is not None:
        return QualitySearchResult(
            status=QualitySearchStatus.UNSUPPORTED,
            encoder_name=item.encoder_info.encoder_name,
            backend=item.encoder_info.backend,
            reason=unsupported,
        )
    model_spec = select_vmaf_model(item.media_info)
    runtime_support = select_vmaf_runtime(ffmpeg_path, model_spec)

    analysis_capabilities = detect_analysis_capabilities(ffmpeg_path)
    active_cpu_vmaf_jobs = analysis_concurrency_limit()
    profile = item.options.analysis_settings
    exact_plan = build_analysis_execution_plan(
        tier=AnalysisTier.EXACT,
        encoder_info=item.encoder_info,
        production_preset=item.options.encoder_preset,
        production_two_pass=item.options.two_pass,
        capabilities=analysis_capabilities,
        decode_policy=AnalysisDecodePolicy.AUTO,
        vmaf_backend=runtime_support.backend,
        active_cpu_vmaf_jobs=active_cpu_vmaf_jobs,
        coarse_vmaf_subsample=profile.coarse_vmaf_subsample,
        exact_vmaf_subsample=profile.exact_vmaf_subsample,
    )
    fingerprint = quality_configuration_fingerprint(
        ffmpeg_path,
        item,
        vmaf_backend=exact_plan.vmaf_backend,
        vmaf_subsample=exact_plan.vmaf_subsample,
    )
    measurement_fingerprint = measurement_configuration_fingerprint(
        ffmpeg_path,
        item,
        vmaf_backend=exact_plan.vmaf_backend,
        vmaf_subsample=exact_plan.vmaf_subsample,
    )
    if not runtime_support.runnable:
        return QualitySearchResult(
            status=QualitySearchStatus.UNSUPPORTED,
            encoder_name=item.encoder_info.encoder_name,
            backend=item.encoder_info.backend,
            measurement_fingerprint=measurement_fingerprint,
            fingerprint=fingerprint,
            reason=_unsupported_reason(item, runtime_support),
        )
    cached = item.quality_search_result
    if cached is not None and cached.fingerprint == fingerprint:
        return cached
    windows = choose_smart_sample_windows(item.media_info.duration, profile)
    initial_candidates: list[QualityCandidateResult] = []
    completed_search_fingerprint = ""
    if cached is not None and cached.measurement_fingerprint == measurement_fingerprint:
        initial_candidates = list(cached.candidates)
    else:
        receipt = load_analysis_receipt(workdir, measurement_fingerprint)
        if (
            receipt is not None
            and receipt.sample_scheme_version == SMART_SAMPLE_SCHEME_VERSION
            and receipt.sample_windows == [(window.start_sec, window.duration_sec) for window in windows]
        ):
            initial_candidates = list(receipt.candidates)
            completed_search_fingerprint = receipt.search_fingerprint

    budget = calculate_smart_bitrate_budget(item)
    if budget.max_video_bitrate_bps < budget.min_video_bitrate_bps:
        return reselect_from_candidates(
            initial_candidates,
            item,
            measurement_fingerprint=measurement_fingerprint,
            fingerprint=fingerprint,
        )

    if initial_candidates:
        reused = reselect_from_candidates(
            initial_candidates,
            item,
            measurement_fingerprint=measurement_fingerprint,
            fingerprint=fingerprint,
        )
        if completed_search_fingerprint == fingerprint:
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "analysis",
                        "state": "receipt_loaded",
                        "reused_candidate_count": len(initial_candidates),
                        "file_name": item.source_path.name,
                        "file_path": str(item.source_path),
                    }
                )
            return reused

    if progress_callback is not None and initial_candidates:
        progress_callback(
            {
                "stage": "analysis",
                "state": "receipt_loaded",
                "reused_candidate_count": len(initial_candidates),
                "file_name": item.source_path.name,
                "file_path": str(item.source_path),
            }
        )

    workdir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    candidates: list[QualityCandidateResult] = list(initial_candidates)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="smart-analysis-", dir=workdir) as temp_dir:
        temp_root = Path(temp_dir)
        references: list[Path] = []
        candidate_indexes = {
            AnalysisTier.COARSE: 0,
            AnalysisTier.EXACT: 0,
        }
        hardest_window = 0
        coarse_plan = build_analysis_execution_plan(
            tier=AnalysisTier.COARSE,
            encoder_info=item.encoder_info,
            production_preset=item.options.encoder_preset,
            production_two_pass=item.options.two_pass,
            capabilities=analysis_capabilities,
            decode_policy=AnalysisDecodePolicy.AUTO,
            vmaf_backend=runtime_support.backend,
            active_cpu_vmaf_jobs=active_cpu_vmaf_jobs,
            coarse_vmaf_subsample=profile.coarse_vmaf_subsample,
            exact_vmaf_subsample=profile.exact_vmaf_subsample,
        )
        with log_path.open("a", encoding="utf-8") as log_file:
            _write_analysis_header(log_file, item, windows, exact_plan)

            def ensure_references(plan: AnalysisExecutionPlan) -> AnalysisExecutionPlan:
                nonlocal exact_plan, coarse_plan
                if references:
                    return plan
                active_plan = plan
                for index, window in enumerate(windows):
                    reference_path = temp_root / f"reference-{index}.mkv"
                    extract_started = time.perf_counter()
                    command = _build_reference(
                        ffmpeg_path,
                        item,
                        window,
                        reference_path,
                        decode_acceleration=active_plan.source_decode_acceleration,
                    )
                    try:
                        _run_logged(
                            command,
                            log_file,
                            cancel_check=cancel_check,
                            process_callback=process_callback,
                            phase="reference extraction",
                        )
                    except SmartCommandError:
                        if active_plan.source_decode_acceleration == SOURCE_DECODE_SOFTWARE:
                            raise
                        reason = (
                            f"{active_plan.source_decode_acceleration} source decode failed; "
                            "retrying with software"
                        )
                        _log_timing(log_file, reason)
                        active_plan = software_source_plan(active_plan, reason=reason)
                        exact_plan = software_source_plan(exact_plan, reason=reason)
                        coarse_plan = software_source_plan(coarse_plan, reason=reason)
                        _run_logged(
                            _build_reference(
                                ffmpeg_path,
                                item,
                                window,
                                reference_path,
                                decode_acceleration=SOURCE_DECODE_SOFTWARE,
                            ),
                            log_file,
                            cancel_check=cancel_check,
                            process_callback=process_callback,
                            phase="reference extraction",
                        )
                    _log_timing(
                        log_file,
                        f"reference extraction #{index + 1}: {time.perf_counter() - extract_started:.2f}s",
                    )
                    references.append(reference_path)
                return active_plan

            def evaluate(bitrate_bps: int, plan: AnalysisExecutionPlan) -> QualityCandidateResult:
                nonlocal hardest_window, exact_plan, coarse_plan
                active_plan = ensure_references(plan)
                if plan.tier == AnalysisTier.EXACT:
                    exact_plan = active_plan
                else:
                    coarse_plan = active_plan
                candidate_indexes[plan.tier] += 1
                candidate_index = candidate_indexes[plan.tier]
                limit = (
                    profile.exact_max_candidates
                    if plan.tier == AnalysisTier.EXACT
                    else profile.coarse_max_candidates
                )
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "analysis",
                            "state": "analyzing",
                            "candidate_index": candidate_index,
                            "candidate_limit": limit,
                            "candidate_bitrate_bps": bitrate_bps,
                            "candidate_tier": plan.tier.value,
                            "analysis_backend": plan.analysis_backend,
                            "decode_backend": plan.source_decode_acceleration,
                            "vmaf_backend": plan.vmaf_backend.value,
                            "n_threads": plan.vmaf_threads,
                            "n_subsample": plan.vmaf_subsample,
                            "reused_candidate_count": len(initial_candidates),
                            "file_name": item.source_path.name,
                            "file_path": str(item.source_path),
                        }
                    )
                order = _window_order(len(windows), hardest_window)
                try:
                    if active_plan.use_loopback:
                        try:
                            result = _score_candidate_loopback(
                                ffmpeg_path,
                                item,
                                windows,
                                bitrate_bps,
                                temp_root,
                                log_file,
                                active_plan,
                                audio_bitrate_bps=budget.audio_bitrate_bps,
                                source_bytes=budget.source_bytes,
                                cancel_check=cancel_check,
                                process_callback=process_callback,
                                min_vmaf_target=float(item.options.min_vmaf),
                                window_order=order,
                            )
                        except (SmartCommandError, RuntimeError) as exc:
                            reason = f"loopback scoring failed; using legacy FFV1 path ({exc})"
                            _log_timing(log_file, reason)
                            active_plan = legacy_loopback_plan(active_plan, reason=reason)
                            exact_plan = legacy_loopback_plan(exact_plan, reason=reason)
                            coarse_plan = legacy_loopback_plan(coarse_plan, reason=reason)
                            active_plan = ensure_references(active_plan)
                            result = _score_candidate(
                                ffmpeg_path,
                                item,
                                references,
                                bitrate_bps,
                                temp_root,
                                workdir,
                                log_file,
                                window_durations_sec=[window.duration_sec for window in windows],
                                audio_bitrate_bps=budget.audio_bitrate_bps,
                                source_bytes=budget.source_bytes,
                                cancel_check=cancel_check,
                                process_callback=process_callback,
                                plan=active_plan,
                                min_vmaf_target=float(item.options.min_vmaf),
                                window_order=order,
                            )
                    else:
                        result = _score_candidate(
                            ffmpeg_path,
                            item,
                            references,
                            bitrate_bps,
                            temp_root,
                            workdir,
                            log_file,
                            window_durations_sec=[window.duration_sec for window in windows],
                            audio_bitrate_bps=budget.audio_bitrate_bps,
                            source_bytes=budget.source_bytes,
                            cancel_check=cancel_check,
                            process_callback=process_callback,
                            plan=active_plan,
                            min_vmaf_target=float(item.options.min_vmaf),
                            window_order=order,
                        )
                except SmartCommandError:
                    if active_plan.vmaf_backend != VmafBackend.CUDA:
                        raise
                    reason = "CUDA VMAF failed; retrying with CPU libvmaf"
                    _log_timing(log_file, reason)
                    active_plan = cpu_vmaf_plan(active_plan, reason=reason)
                    exact_plan = cpu_vmaf_plan(exact_plan, reason=reason)
                    coarse_plan = cpu_vmaf_plan(coarse_plan, reason=reason)
                    result = _score_candidate(
                        ffmpeg_path,
                        item,
                        references,
                        bitrate_bps,
                        temp_root,
                        workdir,
                        log_file,
                        window_durations_sec=[window.duration_sec for window in windows],
                        audio_bitrate_bps=budget.audio_bitrate_bps,
                        source_bytes=budget.source_bytes,
                        cancel_check=cancel_check,
                        process_callback=process_callback,
                        plan=active_plan,
                        min_vmaf_target=float(item.options.min_vmaf),
                        window_order=order,
                    )
                if len(result.segment_vmaf) == len(windows):
                    hardest_window = _hardest_window_index(result)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "analysis",
                            "state": "candidate_finished",
                            "candidate_index": candidate_index,
                            "candidate_limit": limit,
                            "candidate_bitrate_bps": bitrate_bps,
                            "candidate_min_vmaf": result.min_vmaf,
                            "candidate_tier": plan.tier.value,
                            "reused_candidate_count": len(initial_candidates),
                            "file_name": item.source_path.name,
                            "file_path": str(item.source_path),
                        }
                    )
                return result

            configured_max = int(item.options.max_video_kbps) * 1_000
            required_ceiling = max(item.media_info.video_bitrate_bps, budget.max_video_bitrate_bps)
            if configured_max > 0:
                required_ceiling = min(required_ceiling, configured_max)
            tolerance = search_tolerance_bps(
                required_ceiling,
                min_bps=profile.min_search_tolerance_bps,
                ratio=profile.search_tolerance_ratio,
            )
            try:
                coarse_candidates: list[QualityCandidateResult] = []
                exact_min = budget.min_video_bitrate_bps
                exact_budget = budget.max_video_bitrate_bps
                exact_ceiling = required_ceiling
                if not initial_candidates:
                    coarse_candidates, _coarse_selected, _coarse_required = search_bitrate_candidates(
                        evaluate=lambda bitrate: evaluate(bitrate, coarse_plan),
                        min_bitrate_bps=budget.min_video_bitrate_bps,
                        budget_bitrate_bps=budget.max_video_bitrate_bps,
                        required_search_ceiling_bps=required_ceiling,
                        min_vmaf=float(item.options.min_vmaf),
                        max_candidates=profile.coarse_max_candidates,
                        max_output_bytes=budget.max_output_bytes,
                        tolerance_bps=tolerance,
                    )
                    exact_min, exact_budget, exact_ceiling = _exact_search_bounds(
                        coarse_candidates,
                        min_bitrate_bps=budget.min_video_bitrate_bps,
                        budget_bitrate_bps=budget.max_video_bitrate_bps,
                        ceiling_bps=required_ceiling,
                        min_vmaf=float(item.options.min_vmaf),
                    )
                searched, _selected_bitrate, _required_bitrate = search_bitrate_candidates(
                    evaluate=lambda bitrate: evaluate(bitrate, exact_plan),
                    min_bitrate_bps=exact_min,
                    budget_bitrate_bps=exact_budget,
                    required_search_ceiling_bps=exact_ceiling,
                    min_vmaf=float(item.options.min_vmaf),
                    max_candidates=profile.exact_max_candidates,
                    max_output_bytes=budget.max_output_bytes,
                    initial_candidates=_refresh_candidate_predictions(
                        initial_candidates, budget, item.media_info.duration
                    ),
                    tolerance_bps=tolerance,
                )
                candidates = _complete_candidates(searched, len(windows))
                if not candidates:
                    candidates = searched
            except _UnsupportedSmartAnalysis as exc:
                return QualitySearchResult(
                    status=QualitySearchStatus.UNSUPPORTED,
                    encoder_name=item.encoder_info.encoder_name,
                    backend=item.encoder_info.backend,
                    measurement_fingerprint=measurement_fingerprint,
                    fingerprint=fingerprint,
                    max_output_bytes=budget.max_output_bytes,
                    reason=str(exc),
                )
            _log_timing(log_file, f"Smart total: {time.perf_counter() - started:.2f}s")

    if exact_plan.vmaf_backend != VmafBackend.CPU or exact_plan.fallback_reason:
        measurement_fingerprint = measurement_configuration_fingerprint(
            ffmpeg_path,
            item,
            vmaf_backend=exact_plan.vmaf_backend,
            vmaf_subsample=exact_plan.vmaf_subsample,
        )
        fingerprint = quality_configuration_fingerprint(
            ffmpeg_path,
            item,
            vmaf_backend=exact_plan.vmaf_backend,
            vmaf_subsample=exact_plan.vmaf_subsample,
        )

    candidates = _refresh_candidate_predictions(candidates, budget, item.media_info.duration)
    persistable = _complete_candidates(candidates, len(windows))
    if persistable:
        try:
            save_analysis_receipt(
                workdir,
                _analysis_receipt(
                    ffmpeg_path,
                    item,
                    measurement_fingerprint,
                    windows,
                    persistable,
                    vmaf_backend=exact_plan.vmaf_backend,
                    vmaf_subsample=exact_plan.vmaf_subsample,
                    search_fingerprint=fingerprint,
                ),
            )
        except (OSError, ValueError) as exc:
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "analysis",
                        "state": "receipt_write_failed",
                        "message": str(exc),
                        "file_name": item.source_path.name,
                        "file_path": str(item.source_path),
                    }
                )
    return reselect_from_candidates(
        persistable or candidates,
        item,
        measurement_fingerprint=measurement_fingerprint,
        fingerprint=fingerprint,
    )


SMART_ANALYSIS_SEMAPHORE = threading.Semaphore(analysis_concurrency_limit())


def acquire_analysis_slot(cancel_check: Callable[[], bool] | None) -> None:
    while not SMART_ANALYSIS_SEMAPHORE.acquire(timeout=0.1):
        if cancel_check is not None and cancel_check():
            raise OperationCancelledError("Smart analysis cancelled.")
