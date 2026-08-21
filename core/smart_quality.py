"""Stable public facade for Smart quality analysis.

New code should import the focused owner module directly. This module keeps
the original public API and temporary test-facing hooks available.
"""

from __future__ import annotations

import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from core import smart_cache as _cache
from core import smart_measurement as _measurement
from core import smart_workflow as _workflow
from core.analysis_concurrency import SMART_ANALYSIS_SEMAPHORE, acquire_analysis_slot
from core.analysis_runtime import build_analysis_execution_plan, detect_analysis_capabilities
from core.models import EncodePlanItem, QualityCandidateResult, QualitySearchResult, VmafBackend
from core.progress_events import ProgressCallback
from core.smart_bitrate import (  # noqa: F401
    CONTAINER_BUDGET_FACTOR,
    DEFAULT_MAX_OUTPUT_RATIO,
    MAX_SEARCH_CANDIDATES,
    SmartBitrateBudget,
    _ceil_candidate,
    _floor_candidate,
    calculate_smart_bitrate_budget,
    parse_bitrate_bps,
    predicted_output_size,
    refresh_candidate_predictions as _refresh_candidate_predictions,
    reselect_from_candidates,
    resolve_max_output_ratio,
    search_bitrate_candidates,
)
from core.smart_cache import SMART_ANALYSIS_ALGORITHM_VERSION, SMART_SAMPLE_SCHEME_VERSION
from core.smart_sampling import discover_sample_plan
from core.subprocess_utils import hidden_popen_kwargs
from core.vmaf_runtime import EXACT_VMAF_SUBSAMPLE, select_vmaf_runtime
from core.constraint_resolution import (
    apply_decision_to_options,
    build_decision_options,
    constraint_policy_from_size_blocked,
    size_blocked_from_constraint_policy,
)


HDR_TRANSFERS = _workflow.HDR_TRANSFERS
SampleWindow = _measurement.SampleWindow
SmartCommandError = _measurement.SmartCommandError
SMART_ERROR_TAIL_CHARS = _measurement.SMART_ERROR_TAIL_CHARS
choose_smart_sample_windows = _workflow.choose_smart_sample_windows
_DEFAULT_SAMPLE_SCHEME_VERSION = SMART_SAMPLE_SCHEME_VERSION
_DEFAULT_ANALYSIS_ALGORITHM_VERSION = SMART_ANALYSIS_ALGORITHM_VERSION
_LEGACY_HOOK_LOCK = threading.RLock()

# Historical private imports remain aliases only; implementations are owned by
# focused modules rather than duplicated here.
_UnsupportedSmartAnalysis = _workflow._UnsupportedSmartAnalysis
_analysis_receipt = _cache.analysis_receipt
_build_loopback_score_command = _measurement.build_loopback_score_command
_build_reference = _measurement.build_reference
_candidate_result = _measurement.candidate_result
_complete_candidates = _workflow._complete_candidates
_emit_analysis_progress = _workflow._emit_analysis_progress
_exact_search_bounds = _workflow._exact_search_bounds
_hardest_window_index = _workflow._hardest_window_index
_log_timing = _measurement.log_timing
_loopback_decoder_name = _measurement.loopback_decoder_name
_sample_window = _workflow._sample_window
_unsupported_reason = _workflow._unsupported_reason
_vmaf_command = _measurement.vmaf_command
_window_order = _workflow._window_order
_write_analysis_header = _workflow._write_analysis_header
_MEASUREMENT_RUN_LOGGED = _measurement.run_logged


@contextmanager
def _cache_versions() -> Iterator[None]:
    """Honor legacy patches to cache-version constants for one call."""
    if (
        SMART_SAMPLE_SCHEME_VERSION == _DEFAULT_SAMPLE_SCHEME_VERSION
        and SMART_ANALYSIS_ALGORITHM_VERSION == _DEFAULT_ANALYSIS_ALGORITHM_VERSION
    ):
        yield
        return
    with _LEGACY_HOOK_LOCK:
        old_sample = _cache.SMART_SAMPLE_SCHEME_VERSION
        old_algorithm = _cache.SMART_ANALYSIS_ALGORITHM_VERSION
        _cache.SMART_SAMPLE_SCHEME_VERSION = SMART_SAMPLE_SCHEME_VERSION
        _cache.SMART_ANALYSIS_ALGORITHM_VERSION = SMART_ANALYSIS_ALGORITHM_VERSION
        try:
            yield
        finally:
            _cache.SMART_SAMPLE_SCHEME_VERSION = old_sample
            _cache.SMART_ANALYSIS_ALGORITHM_VERSION = old_algorithm


def _run_logged(
    cmd: list[str],
    log_file: TextIO,
    *,
    cancel_check: Callable[[], bool] | None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None,
    cwd: Path | None = None,
    phase: str = "command",
    capture_output: bool = False,
) -> str:
    """Legacy hook that forwards process patches to the measurement owner."""
    _measurement.subprocess = subprocess
    _measurement.hidden_popen_kwargs = hidden_popen_kwargs
    return _MEASUREMENT_RUN_LOGGED(
        cmd,
        log_file,
        cancel_check=cancel_check,
        process_callback=process_callback,
        cwd=cwd,
        phase=phase,
        capture_output=capture_output,
    )


def _score_candidate(*args: Any, **kwargs: Any) -> QualityCandidateResult:
    previous = _measurement.run_logged
    _measurement.run_logged = _run_logged
    try:
        return _measurement.score_candidate(*args, **kwargs)
    finally:
        _measurement.run_logged = previous


def _score_candidate_loopback(*args: Any, **kwargs: Any) -> QualityCandidateResult:
    previous = _measurement.run_logged
    _measurement.run_logged = _run_logged
    try:
        return _measurement.score_candidate_loopback(*args, **kwargs)
    finally:
        _measurement.run_logged = previous


_DEFAULT_FACADE_HOOKS = {
    "select_vmaf_runtime": select_vmaf_runtime,
    "detect_analysis_capabilities": detect_analysis_capabilities,
    "discover_sample_plan": discover_sample_plan,
    "build_analysis_execution_plan": build_analysis_execution_plan,
    "search_bitrate_candidates": search_bitrate_candidates,
    "_run_logged": _run_logged,
    "_score_candidate": _score_candidate,
    "_score_candidate_loopback": _score_candidate_loopback,
}


def measurement_configuration_payload(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    *,
    vmaf_backend: VmafBackend = VmafBackend.CPU,
    vmaf_subsample: int = EXACT_VMAF_SUBSAMPLE,
) -> dict[str, object]:
    with _cache_versions():
        return _cache.measurement_configuration_payload(
            ffmpeg_path,
            item,
            vmaf_backend=vmaf_backend,
            vmaf_subsample=vmaf_subsample,
        )


def measurement_configuration_fingerprint(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    *,
    vmaf_backend: VmafBackend = VmafBackend.CPU,
    vmaf_subsample: int = EXACT_VMAF_SUBSAMPLE,
) -> str:
    with _cache_versions():
        return _cache.measurement_configuration_fingerprint(
            ffmpeg_path,
            item,
            vmaf_backend=vmaf_backend,
            vmaf_subsample=vmaf_subsample,
        )


def quality_configuration_fingerprint(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    *,
    vmaf_backend: VmafBackend = VmafBackend.CPU,
    vmaf_subsample: int = EXACT_VMAF_SUBSAMPLE,
    measurement_fingerprint: str | None = None,
) -> str:
    with _cache_versions():
        return _cache.quality_configuration_fingerprint(
            ffmpeg_path,
            item,
            vmaf_backend=vmaf_backend,
            vmaf_subsample=vmaf_subsample,
            measurement_fingerprint=measurement_fingerprint,
        )


@contextmanager
def _legacy_workflow_hooks() -> Iterator[None]:
    """Bridge old facade monkeypatch paths without runtime dependency rebinding."""
    hooks = {
        "select_vmaf_runtime": select_vmaf_runtime,
        "detect_analysis_capabilities": detect_analysis_capabilities,
        "discover_sample_plan": discover_sample_plan,
        "build_analysis_execution_plan": build_analysis_execution_plan,
        "search_bitrate_candidates": search_bitrate_candidates,
        "_run_logged": _run_logged,
        "_score_candidate": _score_candidate,
        "_score_candidate_loopback": _score_candidate_loopback,
    }
    if all(value is _DEFAULT_FACADE_HOOKS[name] for name, value in hooks.items()):
        yield
        return
    # Only tests and legacy embedders that monkeypatch private facade hooks take
    # this path. Keep their temporary global bridge serialized; production
    # analyses call the workflow directly and retain their configured parallelism.
    with _LEGACY_HOOK_LOCK:
        previous = {name: getattr(_workflow, name) for name in hooks}
        for name, value in hooks.items():
            setattr(_workflow, name, value)
        try:
            yield
        finally:
            for name, value in previous.items():
                setattr(_workflow, name, value)


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
    """Run Smart analysis with the historical facade signature."""
    with _cache_versions(), _legacy_workflow_hooks():
        return _workflow.analyze_quality(
            ffmpeg_path,
            item,
            workdir,
            log_path,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            process_callback=process_callback,
        )


__all__ = [
    "SMART_ANALYSIS_ALGORITHM_VERSION",
    "SMART_ANALYSIS_SEMAPHORE",
    "SMART_ERROR_TAIL_CHARS",
    "SMART_SAMPLE_SCHEME_VERSION",
    "SampleWindow",
    "SmartBitrateBudget",
    "SmartCommandError",
    "acquire_analysis_slot",
    "analyze_quality",
    "apply_decision_to_options",
    "build_decision_options",
    "calculate_smart_bitrate_budget",
    "choose_smart_sample_windows",
    "constraint_policy_from_size_blocked",
    "measurement_configuration_fingerprint",
    "measurement_configuration_payload",
    "parse_bitrate_bps",
    "predicted_output_size",
    "quality_configuration_fingerprint",
    "reselect_from_candidates",
    "resolve_max_output_ratio",
    "search_bitrate_candidates",
    "size_blocked_from_constraint_policy",
]
