"""Stable compatibility facade for Smart quality analysis."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from core import smart as _smart
from core.models import EncodePlanItem, QualitySearchResult
from core.progress_events import ProgressCallback
from core.smart import workflow as _workflow


SMART_ANALYSIS_ALGORITHM_VERSION = _smart.SMART_ANALYSIS_ALGORITHM_VERSION
SMART_ANALYSIS_SEMAPHORE = _smart.SMART_ANALYSIS_SEMAPHORE
SMART_ERROR_TAIL_CHARS = _smart.SMART_ERROR_TAIL_CHARS
SMART_SAMPLE_SCHEME_VERSION = _smart.SMART_SAMPLE_SCHEME_VERSION
SampleWindow = _smart.SampleWindow
SmartBitrateBudget = _smart.SmartBitrateBudget
SmartCommandError = _smart.SmartCommandError
acquire_analysis_slot = _smart.acquire_analysis_slot
apply_decision_to_options = _smart.apply_decision_to_options
build_decision_options = _smart.build_decision_options
calculate_smart_bitrate_budget = _smart.calculate_smart_bitrate_budget
choose_smart_sample_windows = _smart.choose_smart_sample_windows
constraint_policy_from_size_blocked = _smart.constraint_policy_from_size_blocked
measurement_configuration_fingerprint = _smart.measurement_configuration_fingerprint
measurement_configuration_payload = _smart.measurement_configuration_payload
parse_bitrate_bps = _smart.parse_bitrate_bps
predicted_output_size = _smart.predicted_output_size
quality_configuration_fingerprint = _smart.quality_configuration_fingerprint
reselect_from_candidates = _smart.reselect_from_candidates
resolve_max_output_ratio = _smart.resolve_max_output_ratio
search_bitrate_candidates = _smart.search_bitrate_candidates
size_blocked_from_constraint_policy = _smart.size_blocked_from_constraint_policy


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
    """Forward the historical public call to the current workflow owner."""
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
