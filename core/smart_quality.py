from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import threading
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Callable, TextIO

from core.build_ffmpeg_cmd import build_encode_commands, build_input_acceleration_args
from core.models import (
    AudioMode,
    CodecChoice,
    CompressionMode,
    ContainerChoice,
    DecodeAcceleration,
    EncodePlanItem,
    OperationCancelledError,
    QualityCandidateResult,
    QualitySearchResult,
    QualitySearchStatus,
    VmafCapabilities,
)
from core.subprocess_utils import hidden_popen_kwargs, noninteractive_run_kwargs


DEFAULT_MAX_OUTPUT_RATIO = {
    CodecChoice.HEVC: 0.70,
    CodecChoice.AV1: 0.50,
}
MAX_SEARCH_CANDIDATES = 8
CONTAINER_BUDGET_FACTOR = 0.98
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
SMART_ERROR_TAIL_CHARS = 4_000


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


def choose_smart_sample_windows(duration_sec: float) -> list[SampleWindow]:
    if duration_sec <= 0:
        raise ValueError("Source duration must be greater than 0.")
    if duration_sec <= 30.0:
        return [SampleWindow(0.0, duration_sec)]

    sample_duration = 10.0
    max_start = duration_sec - sample_duration
    starts = [
        max(0.0, min(max_start, duration_sec * fraction - sample_duration / 2.0))
        for fraction in (0.20, 0.50, 0.80)
    ]
    if any(starts[index + 1] < starts[index] + sample_duration for index in range(2)):
        starts = [0.0, max_start / 2.0, max_start]
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


def _measured_prediction_ratio(candidate: QualityCandidateResult, source_bytes: int) -> float | None:
    if candidate.predicted_output_ratio is not None:
        return candidate.predicted_output_ratio
    if candidate.predicted_output_bytes is not None and source_bytes > 0:
        return candidate.predicted_output_bytes / source_bytes
    return None


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
) -> tuple[list[QualityCandidateResult], int | None, int | None]:
    """Return tested candidates, a selectable bitrate, and required bitrate.

    When ``max_output_bytes`` is supplied, a candidate is selectable only when
    both its VMAF and its measured output estimate satisfy the constraints.
    ``required_bitrate`` intentionally remains the lowest *quality-passing*
    candidate, even when that candidate is too large.  The caller can then
    report the measured ratio that explains why the joint constraints are
    unsatisfiable.
    """
    cache: dict[int, QualityCandidateResult] = {}
    candidate_limit = min(MAX_SEARCH_CANDIDATES, max(0, int(max_candidates)))

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
        bitrate = _floor_candidate(value)
        if bitrate in cache or len(cache) >= candidate_limit:
            return cache.get(bitrate)
        cache[bitrate] = evaluate(bitrate)
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
        while len(cache) < candidate_limit and high - low > 1_000:
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
    while len(cache) < candidate_limit and high - low > 1_000:
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


def quality_configuration_fingerprint(ffmpeg_path: Path, item: EncodePlanItem) -> str:
    if item.encoder_info is None:
        raise ValueError("Smart analysis requires a bound encoder.")
    source_stat = item.source_path.stat()
    try:
        ffmpeg_stat = ffmpeg_path.stat()
        ffmpeg_identity = [str(ffmpeg_path.resolve()), ffmpeg_stat.st_size, ffmpeg_stat.st_mtime_ns]
    except OSError:
        ffmpeg_identity = [str(ffmpeg_path), None, None]
    options = item.options
    payload = {
        "source": [str(item.source_path.resolve()), source_stat.st_size, source_stat.st_mtime_ns],
        "ffmpeg": ffmpeg_identity,
        "codec": options.codec.value,
        "encoder": item.encoder_info.encoder_name,
        "backend": item.encoder_info.backend.value,
        "decode_acceleration": options.decode_acceleration.value,
        "preset": options.encoder_preset,
        "pix_fmt": options.pix_fmt,
        "two_pass": options.two_pass,
        "min_vmaf": options.min_vmaf,
        "max_output_ratio": resolve_max_output_ratio(options.codec, options.max_output_ratio),
        "audio_mode": options.audio_mode.value,
        "audio_bitrate": options.audio_bitrate,
        "min_video_kbps": options.min_video_kbps,
        "max_video_kbps": options.max_video_kbps,
        "maxrate_factor": options.maxrate_factor,
        "bufsize_factor": options.bufsize_factor,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **noninteractive_run_kwargs(),
    )


@lru_cache(maxsize=8)
def _detect_vmaf_cached(ffmpeg_path: str, size: int | None, mtime_ns: int | None) -> VmafCapabilities:
    del size, mtime_ns

    def model_works(model: str) -> tuple[bool, str]:
        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=5:duration=0.4",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=5:duration=0.4",
            "-lavfi",
            f"libvmaf=model=version={model}",
            "-f",
            "null",
            "-",
        ]
        proc = _run_capture(cmd)
        return proc.returncode == 0 and "VMAF score:" in proc.stderr, proc.stderr

    standard, standard_error = model_works("vmaf_v0.6.1")
    if not standard:
        return VmafCapabilities(
            filter_available=False,
            standard_model=False,
            model_4k=False,
            error_message=standard_error.strip() or "libvmaf standard model could not run.",
        )
    model_4k, model_4k_error = model_works("vmaf_4k_v0.6.1")
    return VmafCapabilities(
        filter_available=True,
        standard_model=True,
        model_4k=model_4k,
        error_message=None if model_4k else (model_4k_error.strip() or "VMAF 4K model could not run."),
    )


def detect_vmaf_capabilities(ffmpeg_path: Path) -> VmafCapabilities:
    try:
        stat = ffmpeg_path.stat()
        size, mtime_ns = stat.st_size, stat.st_mtime_ns
    except OSError:
        size, mtime_ns = None, None
    return _detect_vmaf_cached(str(ffmpeg_path), size, mtime_ns)


def _is_4k(item: EncodePlanItem) -> bool:
    media = item.media_info
    if media is None or media.width is None or media.height is None:
        return False
    short_side, long_side = sorted((media.width, media.height))
    return short_side >= 2160 and long_side >= 3840


def _unsupported_reason(item: EncodePlanItem, capabilities: VmafCapabilities) -> str | None:
    media = item.media_info
    if not capabilities.filter_available or not capabilities.standard_model:
        return capabilities.error_message or "libvmaf standard model is unavailable."
    if _is_4k(item) and not capabilities.model_4k:
        return capabilities.error_message or "The VMAF 4K model is unavailable."
    if media and media.color_transfer and media.color_transfer.lower() in HDR_TRANSFERS:
        return f"HDR transfer {media.color_transfer!r} is not supported by smart mode."
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
        command = " ".join(str(part) for part in cmd)
        raise RuntimeError(
            f"Smart {phase} failed to start (exit code unavailable): {command}\n"
            f"Output tail: {str(exc)[-SMART_ERROR_TAIL_CHARS:]}"
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


def _build_reference(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    window: SampleWindow,
    output_path: Path,
) -> list[str]:
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-y",
        *build_input_acceleration_args(item),
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
        "setpts=PTS-STARTPTS",
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-g",
        "1",
        str(output_path),
    ]


def _parse_vmaf_json(path: Path) -> float:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return float(data["pooled_metrics"]["vmaf"]["mean"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"VMAF JSON did not contain a pooled mean: {path}") from exc


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
) -> QualityCandidateResult:
    scores: list[float] = []
    encoded_bytes: list[int] = []
    encoded_durations_sec: list[float] = []
    candidate_options = replace(
        item.options,
        decode_acceleration=DecodeAcceleration.SOFTWARE,
        container=ContainerChoice.MKV,
        copy_subtitles=False,
        copy_external_subtitles=False,
        overwrite=True,
    )
    candidate_item = replace(
        item,
        options=candidate_options,
        target_video_bitrate_bps=bitrate_bps,
    )
    model = "vmaf_4k_v0.6.1" if _is_4k(item) else "vmaf_v0.6.1"
    for index, reference in enumerate(references):
        candidate_path = temp_root / f"candidate-{bitrate_bps}-{index}.mkv"
        commands, passlog = build_encode_commands(
            ffmpeg_path,
            candidate_item,
            workdir,
            input_path=reference,
            output_path=candidate_path,
            stage=f"smart-{bitrate_bps}-{index}",
        )
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
                for candidate in passlog.parent.glob(passlog.name + "*"):
                    try:
                        candidate.unlink()
                    except OSError:
                        pass
        try:
            encoded_size = candidate_path.stat().st_size
        except OSError as exc:
            raise RuntimeError(
                f"Smart candidate encode did not produce its sample output: {candidate_path}"
            ) from exc
        duration = (
            window_durations_sec[index]
            if window_durations_sec is not None and index < len(window_durations_sec)
            else (item.media_info.duration if item.media_info is not None else 0.0)
        )
        if duration <= 0:
            raise RuntimeError(f"Smart candidate encode produced an invalid sample duration: {duration}")
        encoded_bytes.append(encoded_size)
        encoded_durations_sec.append(float(duration))
        json_path = temp_root / f"vmaf-{bitrate_bps}-{index}.json"
        # FFmpeg's filtergraph parser has its own escaping rules.  A Windows
        # drive-qualified absolute path can therefore be interpreted as a
        # malformed option even when it is correctly escaped for Python.  The
        # scoring process runs in the analysis temp directory, so a generated
        # basename is both safe and unambiguous.
        json_name = json_path.name
        filter_graph = (
            "[0:v]setpts=PTS-STARTPTS[dist];"
            "[1:v]setpts=PTS-STARTPTS[ref];"
            f"[dist][ref]libvmaf=model=version={model}:log_fmt=json:log_path='{json_name}'"
        )
        score_command = [
            str(ffmpeg_path),
            "-hide_banner",
            "-y",
            "-i",
            str(candidate_path),
            "-i",
            str(reference),
            "-filter_complex",
            filter_graph,
            "-an",
            "-f",
            "null",
            "-",
        ]
        _run_logged(
            score_command,
            log_file,
            cancel_check=cancel_check,
            process_callback=process_callback,
            cwd=temp_root,
            phase="VMAF scoring",
        )
        scores.append(_parse_vmaf_json(json_path))

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

    fingerprint = quality_configuration_fingerprint(ffmpeg_path, item)
    cached = item.quality_search_result
    if cached is not None and cached.fingerprint == fingerprint:
        return cached

    capabilities = detect_vmaf_capabilities(ffmpeg_path)
    unsupported = _unsupported_reason(item, capabilities)
    budget = calculate_smart_bitrate_budget(item)
    base = {
        "encoder_name": item.encoder_info.encoder_name,
        "backend": item.encoder_info.backend,
        "fingerprint": fingerprint,
        "max_output_bytes": budget.max_output_bytes,
    }
    if unsupported:
        return QualitySearchResult(
            status=QualitySearchStatus.UNSUPPORTED,
            reason=unsupported,
            **base,
        )
    if budget.max_video_bitrate_bps < budget.min_video_bitrate_bps:
        return QualitySearchResult(
            status=QualitySearchStatus.CONSTRAINT_UNSATISFIED,
            reason="Audio and container overhead leave too little room for the minimum video bitrate.",
            **base,
        )

    workdir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smart-analysis-", dir=workdir) as temp_dir:
        temp_root = Path(temp_dir)
        references: list[Path] = []
        windows = choose_smart_sample_windows(item.media_info.duration)
        with log_path.open("a", encoding="utf-8") as log_file:
            for index, window in enumerate(windows):
                reference_path = temp_root / f"reference-{index}.mkv"
                _run_logged(
                    _build_reference(ffmpeg_path, item, window, reference_path),
                    log_file,
                    cancel_check=cancel_check,
                    process_callback=process_callback,
                    phase="reference extraction",
                )
                references.append(reference_path)

            candidate_index = 0

            def evaluate(bitrate_bps: int) -> QualityCandidateResult:
                nonlocal candidate_index
                candidate_index += 1
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "analysis",
                            "state": "analyzing",
                            "candidate_index": candidate_index,
                            "candidate_limit": MAX_SEARCH_CANDIDATES,
                            "candidate_bitrate_bps": bitrate_bps,
                            "file_name": item.source_path.name,
                            "file_path": str(item.source_path),
                        }
                    )
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
                )
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "analysis",
                            "state": "candidate_finished",
                            "candidate_index": candidate_index,
                            "candidate_limit": MAX_SEARCH_CANDIDATES,
                            "candidate_bitrate_bps": bitrate_bps,
                            "candidate_min_vmaf": result.min_vmaf,
                            "file_name": item.source_path.name,
                            "file_path": str(item.source_path),
                        }
                    )
                return result

            configured_max = int(item.options.max_video_kbps) * 1_000
            required_ceiling = max(item.media_info.video_bitrate_bps, budget.max_video_bitrate_bps)
            if configured_max > 0:
                required_ceiling = min(required_ceiling, configured_max)
            candidates, selected_bitrate, required_bitrate = search_bitrate_candidates(
                evaluate=evaluate,
                min_bitrate_bps=budget.min_video_bitrate_bps,
                budget_bitrate_bps=budget.max_video_bitrate_bps,
                required_search_ceiling_bps=required_ceiling,
                min_vmaf=float(item.options.min_vmaf),
                max_output_bytes=budget.max_output_bytes,
            )

    if selected_bitrate is not None:
        chosen = next(
            candidate for candidate in candidates
            if candidate.video_bitrate_bps == selected_bitrate
            and candidate.min_vmaf >= item.options.min_vmaf
        )
        return QualitySearchResult(
            status=QualitySearchStatus.FOUND,
            candidates=candidates,
            selected_video_bitrate_bps=selected_bitrate,
            min_vmaf=chosen.min_vmaf,
            predicted_output_bytes=chosen.predicted_output_bytes,
            predicted_output_ratio=_measured_prediction_ratio(chosen, budget.source_bytes),
            **base,
        )

    required_ratio = None
    if required_bitrate is not None:
        required_candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate.video_bitrate_bps == required_bitrate
                and candidate.min_vmaf >= item.options.min_vmaf
            ),
            None,
        )
        if required_candidate is not None:
            required_ratio = _measured_prediction_ratio(required_candidate, budget.source_bytes)
    cap_candidate = next(
        (candidate for candidate in candidates if candidate.video_bitrate_bps == _floor_candidate(budget.max_video_bitrate_bps)),
        None,
    )
    size_blocked = required_ratio is not None and required_ratio > (
        budget.max_output_bytes / budget.source_bytes
    )
    return QualitySearchResult(
        status=QualitySearchStatus.CONSTRAINT_UNSATISFIED,
        candidates=candidates,
        min_vmaf=cap_candidate.min_vmaf if cap_candidate else None,
        required_output_ratio=required_ratio,
        reason=(
            (
                f"VMAF {item.options.min_vmaf:.1f} requires an estimated output ratio of "
                f"{required_ratio:.3f}, above the configured size limit."
            )
            if size_blocked
            else f"Maximum allowed output cannot reach VMAF {item.options.min_vmaf:.1f}."
            if required_ratio is not None
            else f"The bound encoder cannot reach VMAF {item.options.min_vmaf:.1f} within the search ceiling."
        ),
        **base,
    )


SMART_ANALYSIS_SEMAPHORE = threading.Semaphore(1)


def acquire_analysis_slot(cancel_check: Callable[[], bool] | None) -> None:
    while not SMART_ANALYSIS_SEMAPHORE.acquire(timeout=0.1):
        if cancel_check is not None and cancel_check():
            raise OperationCancelledError("Smart analysis cancelled.")
