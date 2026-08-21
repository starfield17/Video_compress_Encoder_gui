"""Smart-analysis lifecycle orchestration.

This module coordinates sampling, measurement, cache reuse, holdout validation,
and refinement. Bitrate policy, receipt identity, and FFmpeg/VMAF mechanics
live in their focused sibling modules.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, TextIO, cast

from .concurrency import analysis_concurrency_limit
from .receipts import load_analysis_receipt, save_analysis_receipt
from .runtime import (
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
)
from core.models import (
    AnalysisProfileSettings,
    AnalysisReceipt,
    CompressionMode,
    ConstraintFailureKind,
    EncodePlanItem,
    OperationCancelledError,
    QualityCandidateResult,
    QualitySearchResult,
    QualitySearchStatus,
    VmafBackend,
    VmafRuntimeSupport,
)
from .sampling.complexity import ComplexityProbeError
from .sampling.planner import (
    PlannedWindow,
    SamplePlan,
    SamplePlanningError,
    ScoutObservation,
    planned_window_from_payload,
    scout_observation_from_payload,
    rank_scout_observations,
    search_window_count,
    should_analyze_whole_video,
)
from core.progress_events import ProgressCallback, ProgressEvent
from .bitrate import (
    calculate_smart_bitrate_budget,
    refresh_candidate_predictions as _refresh_candidate_predictions,
    reselect_from_candidates,
    rd_ambiguity_events,
    search_bitrate_candidates,
    predicted_output_size,
)
from .cache import (
    SMART_SAMPLE_SCHEME_VERSION,
    analysis_receipt as _analysis_receipt,
    measurement_configuration_fingerprint,
    quality_configuration_fingerprint,
)
from .measurement import (
    SampleWindow,
    SmartCommandError,
    build_reference as _build_reference,
    log_timing as _log_timing,
    run_logged as _run_logged,
    score_candidate as _score_candidate,
    score_candidate_loopback as _score_candidate_loopback,
    measure_size_only as _measure_size_only,
)
from .sampling.scout import discover_sample_plan
from .size_prediction import predict_size_distribution
from .vmaf import (
    VMAF_MEASUREMENT_BIT_DEPTH,
    VMAF_MEASUREMENT_PIX_FMT,
    candidate_encode_metadata,
    select_vmaf_model,
    select_vmaf_runtime,
)


HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


class _UnsupportedSmartAnalysis(RuntimeError):
    pass


def choose_smart_sample_windows(
    duration_sec: float,
    settings: AnalysisProfileSettings | None = None,
) -> list[SampleWindow]:
    """Return deterministic legacy windows when a caller does not use Scout."""
    if duration_sec <= 0:
        raise ValueError("Source duration must be greater than 0.")
    profile = settings or AnalysisProfileSettings()
    if should_analyze_whole_video(duration_sec, profile):
        return [SampleWindow(0.0, duration_sec)]
    window_count = search_window_count(duration_sec, profile)
    sample_duration = min(profile.sample_duration_sec, duration_sec / window_count)
    max_start = max(0.0, duration_sec - sample_duration)
    if window_count == 1:
        return [SampleWindow(max_start / 2.0, sample_duration)]
    fractions = tuple((index + 1) / (window_count + 1) for index in range(window_count))
    starts = [
        max(0.0, min(max_start, duration_sec * fraction - sample_duration / 2.0))
        for fraction in fractions
    ]
    if any(starts[index + 1] < starts[index] + sample_duration for index in range(len(starts) - 1)):
        starts = [max_start * index / max(window_count - 1, 1) for index in range(window_count)]
    return [SampleWindow(start, sample_duration) for start in starts]


def _unsupported_reason(item: EncodePlanItem, support: VmafRuntimeSupport | None = None) -> str | None:
    media = item.media_info
    if media and media.color_transfer and media.color_transfer.lower() in HDR_TRANSFERS:
        return f"HDR transfer {media.color_transfer!r} is not supported by smart mode."
    if support is not None and not support.runnable:
        return support.error_message or f"VMAF model {support.model} is unavailable on {support.backend.value}."
    return None


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


def _predicted_threshold_bitrate(
    candidates: list[QualityCandidateResult], target: float
) -> int | None:
    ordered = sorted(candidates, key=lambda value: value.video_bitrate_bps)
    for lower, upper in zip(ordered, ordered[1:]):
        if lower.min_vmaf < target <= upper.min_vmaf:
            delta = upper.min_vmaf - lower.min_vmaf
            if delta <= 1e-9:
                return upper.video_bitrate_bps
            fraction = (target - lower.min_vmaf) / delta
            return round(
                lower.video_bitrate_bps
                + fraction * (upper.video_bitrate_bps - lower.video_bitrate_bps)
            )
    passing = [value for value in ordered if value.min_vmaf >= target]
    return passing[0].video_bitrate_bps if passing else None


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


def _sample_window(window: PlannedWindow) -> SampleWindow:
    return SampleWindow(window.start_sec, window.duration_sec)


def _emit_analysis_progress(
    callback: ProgressCallback | None,
    item: EncodePlanItem,
    state: str,
    **values: object,
) -> None:
    if callback is None:
        return
    event = {
        "stage": "analysis",
        "state": state,
        "file_name": item.source_path.name,
        "file_path": str(item.source_path),
        **values,
    }
    callback(cast(ProgressEvent, event))


def _write_analysis_header(
    log_file: TextIO,
    item: EncodePlanItem,
    windows: list[SampleWindow],
    plan: AnalysisExecutionPlan,
) -> None:
    if item.media_info is None:
        raise ValueError("Smart analysis requires probed media.")
    model_spec = select_vmaf_model(item.media_info, item.options.viewing_context)
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
        f"viewing_context={item.options.viewing_context.value}\n"
        f"vmaf_hfr={'yes' if model_spec.hfr else 'no'}\n"
        f"vmaf_display={model_spec.display_width}x{model_spec.display_height}\n"
        f"vmaf_measurement={VMAF_MEASUREMENT_PIX_FMT}/{VMAF_MEASUREMENT_BIT_DEPTH}-bit\n"
        f"candidate_encode={metadata.width}x{metadata.height}/{metadata.bit_depth}-bit\n"
        f"source_geometry={item.media_info.width}x{item.media_info.height}\n"
        f"source_bit_depth={item.media_info.bit_depth}\n"
        "pooling=smart_v2_temporal_mean_worst_1s_v1\n"
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
    progress_callback: ProgressCallback | None = None,
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
    model_spec = select_vmaf_model(item.media_info, item.options.viewing_context)
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
    initial_candidates: list[QualityCandidateResult] = []
    completed_search_fingerprint = ""
    receipt: AnalysisReceipt | None = None
    if cached is not None and cached.measurement_fingerprint == measurement_fingerprint:
        initial_candidates = list(cached.candidates)
    else:
        receipt = load_analysis_receipt(workdir, measurement_fingerprint)
        if (
            receipt is not None
            and receipt.sample_scheme_version == SMART_SAMPLE_SCHEME_VERSION
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
        scout_observations: list[ScoutObservation]
        if receipt is not None and receipt.search_windows:
            planned_search = [planned_window_from_payload(value) for value in receipt.search_windows]
            planned_holdouts = [planned_window_from_payload(value) for value in receipt.holdout_windows]
            planned_reserves = [planned_window_from_payload(value) for value in receipt.reserve_windows]
            scout_observations = [
                scout_observation_from_payload(value) for value in receipt.scout_windows
            ]
            sample_plan = SamplePlan(
                scout_windows=tuple(value.window for value in scout_observations),
                search_windows=tuple(planned_search),
                holdout_windows=tuple(planned_holdouts),
                whole_video=not scout_observations,
                reserve_windows=tuple(planned_reserves),
                content_uncertainty=receipt.content_uncertainty,
                content_heterogeneity=receipt.content_heterogeneity,
            )
        else:
            sample_plan = SamplePlan((), (), (), False)
            scout_observations = []
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
            if not sample_plan.search_windows:
                def run_sampling_command(command: list[str], phase: str) -> None:
                    _run_logged(
                        command,
                        log_file,
                        cancel_check=cancel_check,
                        process_callback=process_callback,
                        phase=phase,
                    )

                try:
                    sampling = discover_sample_plan(
                        ffmpeg_path=ffmpeg_path,
                        source_path=item.source_path,
                        source_duration_sec=item.media_info.duration,
                        settings=profile,
                        temp_root=temp_root,
                        run_command=run_sampling_command,
                        progress=lambda state, values: _emit_analysis_progress(
                            progress_callback, item, state, **values
                        ),
                    )
                    sample_plan = sampling.plan
                    scout_observations = list(sampling.observations)
                except OperationCancelledError:
                    raise
                except (
                    ComplexityProbeError,
                    SamplePlanningError,
                    SmartCommandError,
                    RuntimeError,
                ) as exc:
                    return QualitySearchResult(
                        status=QualitySearchStatus.FAILED,
                        encoder_name=item.encoder_info.encoder_name,
                        backend=item.encoder_info.backend,
                        measurement_fingerprint=measurement_fingerprint,
                        fingerprint=fingerprint,
                        reason=f"Smart content scout failed: {exc}",
                    )
            planned_search = list(sample_plan.search_windows)
            planned_holdouts = list(sample_plan.holdout_windows)
            planned_reserves = list(sample_plan.reserve_windows)
            windows = [_sample_window(window) for window in planned_search]
            _write_analysis_header(log_file, item, windows, exact_plan)
            log_file.write(
                f"scout_windows={len(scout_observations)}\n"
                f"search_windows={len(planned_search)}\n"
                f"holdout_windows={len(planned_holdouts)}\n"
                f"reserve_windows={len(planned_reserves)}\n"
            )
            for window in [*planned_search, *planned_holdouts, *planned_reserves]:
                log_file.write(
                    f"sample={window.id} start={window.start_sec:.3f} duration={window.duration_sec:.3f} "
                    f"reasons={','.join(window.reasons)} crosses_scene_cut={window.crosses_scene_cut}\n"
                )
            log_file.flush()

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
                if (
                    len(result.observed_window_bitrates) == len(planned_search)
                    and len(windows) == len(planned_search)
                    and scout_observations
                ):
                    assert item.media_info is not None
                    ranked_risks = rank_scout_observations(scout_observations)
                    risk_by_id = {
                        value.observation.window.id: value.risk.global_risk
                        for value in ranked_risks
                    }
                    timeline_risks = [value.risk.global_risk for value in ranked_risks]
                    sample_risks = [
                        risk_by_id.get(window.scout_id or "", 0.5)
                        for window in planned_search
                    ]
                    prediction = predict_size_distribution(
                        requested_bitrate_bps=result.video_bitrate_bps,
                        observed_window_bitrates=result.observed_window_bitrates,
                        duration_sec=item.media_info.duration,
                        audio_bitrate_bps=budget.audio_bitrate_bps,
                        source_bytes=budget.source_bytes,
                        sample_risks=sample_risks,
                        timeline_risks=timeline_risks,
                    )
                    result = replace(
                        result,
                        observed_video_bitrate_bps=prediction.mean_video_bitrate_bps,
                        predicted_output_bytes=prediction.predicted_output_bytes,
                        predicted_output_ratio=prediction.predicted_output_ratio,
                        size_prediction=prediction,
                    )
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

            def evaluate_planned_subset(
                bitrate_bps: int,
                subset: list[PlannedWindow],
            ) -> QualityCandidateResult:
                nonlocal windows, references, hardest_window
                saved_windows = windows
                saved_references = references
                saved_hardest = hardest_window
                candidate_indexes[AnalysisTier.EXACT] = 0
                windows = [_sample_window(window) for window in subset]
                references = []
                hardest_window = 0
                try:
                    return evaluate(bitrate_bps, exact_plan)
                finally:
                    windows = saved_windows
                    references = saved_references
                    hardest_window = saved_hardest

            def verify_holdouts(
                bitrate_bps: int,
                holdouts: list[PlannedWindow],
                *,
                refinement_round: int,
            ) -> tuple[list[PlannedWindow], list[float]]:
                failed: list[PlannedWindow] = []
                scores: list[float] = []
                for holdout_index, holdout in enumerate(holdouts):
                    _emit_analysis_progress(
                        progress_callback,
                        item,
                        "holdout_verification",
                        holdout_index=holdout_index + 1,
                        holdout_count=len(holdouts),
                        refinement_round=refinement_round,
                        candidate_bitrate_bps=bitrate_bps,
                    )
                    result = evaluate_planned_subset(bitrate_bps, [holdout])
                    score = result.min_vmaf
                    scores.append(score)
                    passed = score >= float(item.options.min_vmaf)
                    log_file.write(
                        f"holdout={holdout.id} bitrate={bitrate_bps} VMAF={score:.3f} "
                        f"result={'PASS' if passed else 'FAIL'}\n"
                    )
                    log_file.flush()
                    if not passed:
                        failed.append(holdout)
                return failed, scores

            configured_max = int(item.options.max_video_kbps) * 1_000
            required_ceiling = max(item.media_info.video_bitrate_bps, budget.max_video_bitrate_bps)
            if configured_max > 0:
                required_ceiling = min(required_ceiling, configured_max)
            tolerance = search_tolerance_bps(
                required_ceiling,
                min_bps=profile.min_search_tolerance_bps,
                ratio=profile.search_tolerance_ratio,
            )
            refinement_records: list[dict[str, object]] = []
            holdout_min_vmaf: float | None = None
            terminal_result: QualitySearchResult | None = None
            remaining_reserves = list(planned_reserves)
            adaptive_expansion_events: list[dict[str, object]] = []
            size_calibration_records: list[dict[str, object]] = []
            search_history_scout_ids = {
                window.scout_id for window in planned_search if window.scout_id is not None
            }
            try:
                _emit_analysis_progress(progress_callback, item, "searching")
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
                    preferred_first_bitrate_bps=_predicted_threshold_bitrate(
                        coarse_candidates, float(item.options.min_vmaf)
                    ),
                )
                candidates = _complete_candidates(searched, len(windows))
                if not candidates:
                    candidates = searched

                selection = reselect_from_candidates(
                    candidates,
                    item,
                    measurement_fingerprint=measurement_fingerprint,
                    fingerprint=fingerprint,
                )
                if (
                    selection.failure_kind == ConstraintFailureKind.SIZE_BLOCKED
                    and remaining_reserves
                ):
                    required_candidate = next(
                        (
                            candidate
                            for candidate in candidates
                            if candidate.video_bitrate_bps == selection.selected_video_bitrate_bps
                        ),
                        None,
                    )
                    prediction = required_candidate.size_prediction if required_candidate else None
                    if prediction is not None and required_candidate is not None:
                        assert item.media_info is not None
                        media_duration = item.media_info.duration
                        central_bytes = predicted_output_size(
                            prediction.mean_video_bitrate_bps,
                            budget.audio_bitrate_bps,
                            media_duration,
                        )
                        near_boundary = (
                            central_bytes <= budget.max_output_bytes < prediction.predicted_output_bytes
                        )
                        if near_boundary:
                            if scout_observations:
                                ranked = rank_scout_observations(scout_observations)
                                reserve_risks = {
                                    value.observation.window.id: value.risk.global_risk
                                    for value in ranked
                                }
                                ordered_risks = sorted(reserve_risks.values())
                                median_risk = ordered_risks[len(ordered_risks) // 2]
                                representatives = sorted(
                                    remaining_reserves,
                                    key=lambda window: (
                                        abs(reserve_risks.get(window.scout_id or "", 0.5) - median_risk),
                                        abs(window.center_sec - media_duration / 2.0),
                                    ),
                                )[:2]
                            else:
                                representatives = sorted(
                                    remaining_reserves,
                                    key=lambda window: abs(window.center_sec - media_duration / 2.0),
                                )[:2]
                            measured = _measure_size_only(
                                ffmpeg_path,
                                item,
                                [_sample_window(window) for window in representatives],
                                required_candidate.video_bitrate_bps,
                                temp_root,
                                log_file,
                                exact_plan,
                                cancel_check=cancel_check,
                                process_callback=process_callback,
                            )
                            required_candidate.observed_window_bitrates.extend(measured)
                            required_candidate.size_prediction = None
                            size_calibration_records.extend(
                                {
                                    "window_id": window.id,
                                    "observed_video_bitrate_bps": bitrate,
                                }
                                for window, bitrate in zip(representatives, measured)
                            )
                            candidates = _refresh_candidate_predictions(
                                candidates, budget, item.media_info.duration
                            )
                            selection = reselect_from_candidates(
                                candidates,
                                item,
                                measurement_fingerprint=measurement_fingerprint,
                                fingerprint=fingerprint,
                            )
                if (
                    selection.success
                    and selection.min_vmaf is not None
                    and selection.min_vmaf - float(item.options.min_vmaf)
                    < profile.quality_confidence_band
                    and remaining_reserves
                ):
                    expanded = max(
                        remaining_reserves,
                        key=lambda window: (len(window.reasons), -window.start_sec),
                    )
                    remaining_reserves.remove(expanded)
                    promoted = replace(
                        expanded,
                        id=f"search:adaptive:{expanded.id}",
                        reasons=tuple(dict.fromkeys((*expanded.reasons, "adaptive_near_threshold"))),
                    )
                    planned_search.append(promoted)
                    if promoted.scout_id is not None:
                        search_history_scout_ids.add(promoted.scout_id)
                    adaptive_expansion_events.append(
                        {
                            "reason": "quality_score_near_threshold",
                            "window_id": promoted.id,
                            "selected_score": selection.min_vmaf,
                        }
                    )
                    windows = [_sample_window(window) for window in planned_search]
                    references = []
                    hardest_window = 0
                    candidate_indexes[AnalysisTier.EXACT] = 0
                    expanded_candidates, _, _ = search_bitrate_candidates(
                        evaluate=lambda bitrate: evaluate(bitrate, exact_plan),
                        min_bitrate_bps=selection.selected_video_bitrate_bps,
                        budget_bitrate_bps=max(selection.selected_video_bitrate_bps, budget.max_video_bitrate_bps),
                        required_search_ceiling_bps=required_ceiling,
                        min_vmaf=float(item.options.min_vmaf),
                        max_candidates=profile.exact_max_candidates,
                        max_output_bytes=budget.max_output_bytes,
                        tolerance_bps=tolerance,
                    )
                    candidates = _complete_candidates(expanded_candidates, len(windows)) or expanded_candidates
                    selection = reselect_from_candidates(
                        candidates,
                        item,
                        measurement_fingerprint=measurement_fingerprint,
                        fingerprint=fingerprint,
                    )
                remaining_holdouts = list(planned_holdouts)
                refinement_round = 0
                final_holdout_scores: list[float] = []
                while selection.success and remaining_holdouts:
                    failed, final_holdout_scores = verify_holdouts(
                        selection.selected_video_bitrate_bps,
                        remaining_holdouts,
                        refinement_round=refinement_round,
                    )
                    holdout_min_vmaf = min(final_holdout_scores) if final_holdout_scores else None
                    if not failed:
                        break
                    if refinement_round >= profile.max_refinement_rounds:
                        terminal_result = replace(
                            selection,
                            status=QualitySearchStatus.FAILED,
                            reason=(
                                "Holdout verification still failed after the configured "
                                "refinement limit."
                            ),
                        )
                        break
                    refinement_round += 1
                    _emit_analysis_progress(
                        progress_callback,
                        item,
                        "refining",
                        refinement_round=refinement_round,
                        refinement_limit=profile.max_refinement_rounds,
                        promoted_window_count=len(failed),
                    )
                    refinement_records.append(
                        {
                            "round": refinement_round,
                            "starting_bitrate_bps": selection.selected_video_bitrate_bps,
                            "promoted_window_ids": [window.id for window in failed],
                            "failed_vmaf": [
                                score
                                for window, score in zip(remaining_holdouts, final_holdout_scores)
                                if window in failed
                            ],
                        }
                    )
                    planned_search.extend(
                        replace(
                            window,
                            id=f"search:promoted:{window.id}",
                            reasons=tuple(dict.fromkeys((*window.reasons, "failed_holdout_promoted"))),
                        )
                        for window in failed
                    )
                    search_history_scout_ids.update(
                        window.scout_id for window in failed if window.scout_id is not None
                    )
                    remaining_holdouts = [window for window in remaining_holdouts if window not in failed]
                    if not remaining_holdouts and remaining_reserves:
                        reserve = remaining_reserves.pop(0)
                        fresh = replace(
                            reserve,
                            id=f"holdout:fresh:{reserve.id}",
                            reasons=tuple(
                                dict.fromkeys((*reserve.reasons, "fresh_reserve_holdout"))
                            ),
                        )
                        if fresh.scout_id not in search_history_scout_ids:
                            remaining_holdouts.append(fresh)
                            refinement_records[-1]["fresh_replacement_holdout_ids"] = [fresh.id]
                    if not remaining_holdouts:
                        terminal_result = replace(
                            selection,
                            status=QualitySearchStatus.FAILED,
                            reason=(
                                "Holdout refinement exhausted independent reserve windows; "
                                "normal-confidence success is not valid."
                            ),
                        )
                        break
                    windows = [_sample_window(window) for window in planned_search]
                    references = []
                    hardest_window = 0
                    candidate_indexes[AnalysisTier.EXACT] = 0
                    refined, _refined_selected, _refined_required = search_bitrate_candidates(
                        evaluate=lambda bitrate: evaluate(bitrate, exact_plan),
                        min_bitrate_bps=selection.selected_video_bitrate_bps,
                        budget_bitrate_bps=max(
                            selection.selected_video_bitrate_bps,
                            budget.max_video_bitrate_bps,
                        ),
                        required_search_ceiling_bps=required_ceiling,
                        min_vmaf=float(item.options.min_vmaf),
                        max_candidates=profile.exact_max_candidates,
                        max_output_bytes=budget.max_output_bytes,
                        tolerance_bps=tolerance,
                    )
                    candidates = _complete_candidates(refined, len(windows))
                    selection = reselect_from_candidates(
                        candidates or refined,
                        item,
                        measurement_fingerprint=measurement_fingerprint,
                        fingerprint=fingerprint,
                    )
                    if not selection.success:
                        if selection.failure_kind == ConstraintFailureKind.SIZE_BLOCKED:
                            terminal_result = selection
                        else:
                            terminal_result = QualitySearchResult(
                                status=QualitySearchStatus.FAILED,
                                encoder_name=item.encoder_info.encoder_name,
                                backend=item.encoder_info.backend,
                                candidates=candidates or refined,
                                measurement_fingerprint=measurement_fingerprint,
                                fingerprint=fingerprint,
                                max_output_bytes=budget.max_output_bytes,
                                reason="Promoted holdout windows could not reach the VMAF target.",
                            )
                        break
                if not remaining_holdouts and sample_plan.whole_video:
                    holdout_min_vmaf = None
                ambiguity_records = rd_ambiguity_events(candidates)
                if ambiguity_records:
                    decision_point = min(
                        candidates,
                        key=lambda candidate: abs(candidate.min_vmaf - float(item.options.min_vmaf)),
                    )
                    repeated = evaluate(decision_point.video_bitrate_bps, exact_plan)
                    candidates = [
                        repeated
                        if candidate.video_bitrate_bps == repeated.video_bitrate_bps
                        else candidate
                        for candidate in candidates
                    ]
                    still_ambiguous = rd_ambiguity_events(candidates)
                    for event in ambiguity_records:
                        event["reevaluated_bitrate_bps"] = repeated.video_bitrate_bps
                        event["still_ambiguous"] = bool(still_ambiguous)
                    candidates = [replace(candidate, rd_ambiguous=True) for candidate in candidates]
                    selection = reselect_from_candidates(
                        candidates,
                        item,
                        measurement_fingerprint=measurement_fingerprint,
                        fingerprint=fingerprint,
                    )
                log_file.write(
                    f"selected_bitrate_bps={selection.selected_video_bitrate_bps}\n"
                    f"search_min_vmaf={selection.min_vmaf}\n"
                    f"holdout_min_vmaf={holdout_min_vmaf}\n"
                    f"refinement_rounds={len(refinement_records)}\n"
                )
                log_file.flush()
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
    search_min_vmaf = selection.min_vmaf if selection is not None else None
    completed_fingerprint = (
        fingerprint if terminal_result is None and selection.success else ""
    )
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
                    scout_observations=scout_observations,
                    search_windows=planned_search,
                    holdout_windows=remaining_holdouts,
                    refinement_rounds=refinement_records,
                    search_min_vmaf=search_min_vmaf,
                    holdout_min_vmaf=holdout_min_vmaf,
                    vmaf_backend=exact_plan.vmaf_backend,
                    vmaf_subsample=exact_plan.vmaf_subsample,
                    search_fingerprint=completed_fingerprint,
                    reserve_windows=remaining_reserves,
                    content_uncertainty=sample_plan.content_uncertainty,
                    content_heterogeneity=sample_plan.content_heterogeneity,
                    independent_final_holdout=bool(
                        remaining_holdouts
                        and any(
                            window.scout_id not in search_history_scout_ids
                            for window in remaining_holdouts
                        )
                    ),
                    adaptive_expansion_events=adaptive_expansion_events,
                    rd_ambiguity_events=ambiguity_records,
                    size_calibration_windows=size_calibration_records,
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
    if terminal_result is not None:
        return replace(
            terminal_result,
            candidates=persistable or terminal_result.candidates,
            measurement_fingerprint=measurement_fingerprint,
            fingerprint=fingerprint,
        )
    return reselect_from_candidates(
        persistable or candidates,
        item,
        measurement_fingerprint=measurement_fingerprint,
        fingerprint=fingerprint,
    )
