from __future__ import annotations

import os
from pathlib import Path

from core.external_subtitles import copy_external_subtitles
from core.models import DecisionActionCode, DecisionOption, EncodePlanItem, EncodeResult, QualitySearchResult
from core.smart_quality import (
    apply_decision_to_options,
    quality_configuration_fingerprint,
    reselect_from_candidates,
)


def reselect_after_quality_decision(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    quality: QualitySearchResult,
    decision: DecisionOption,
) -> QualitySearchResult:
    if decision.action_code in {DecisionActionCode.SKIP, DecisionActionCode.REANALYZE}:
        raise ValueError(f"{decision.action_code.value} is a queue action, not a local candidate selection.")
    item.options = apply_decision_to_options(item.options, decision)
    return reselect_from_candidates(
        quality.candidates,
        item,
        measurement_fingerprint=quality.measurement_fingerprint,
        fingerprint=quality_configuration_fingerprint(ffmpeg_path, item),
    )


def _rejected_path(item: EncodePlanItem, result: EncodeResult) -> Path:
    rejected = result.rejected_output_path
    if not result.needs_decision or rejected is None:
        raise ValueError("Encode result does not contain a preserved size miss.")
    if result.output_path != item.output_path:
        raise ValueError("Encode result output does not match the queue item output.")
    return rejected


def accept_rejected_output(item: EncodePlanItem, result: EncodeResult) -> None:
    rejected = _rejected_path(item, result)
    os.replace(rejected, item.output_path)
    result.success = True
    result.skipped = False
    result.needs_decision = False
    result.rejected_output_path = None
    if item.options.copy_external_subtitles:
        copied, warnings = copy_external_subtitles(
            item.source_path,
            item.output_path,
            overwrite=item.options.overwrite,
        )
        result.copied_external_subtitle_paths.extend(copied)
        result.external_subtitle_warnings.extend(warnings)


def discard_rejected_output(item: EncodePlanItem, result: EncodeResult) -> None:
    rejected = _rejected_path(item, result)
    rejected.unlink(missing_ok=True)
    result.success = False
    result.skipped = True
    result.needs_decision = False
    result.rejected_output_path = None


def prepare_size_miss_retry(item: EncodePlanItem, result: EncodeResult) -> int:
    _rejected_path(item, result)
    quality = item.quality_search_result
    if (
        result.actual_output_bytes is None
        or result.allowed_output_bytes is None
        or result.actual_output_bytes <= 0
        or quality is None
        or quality.selected_video_bitrate_bps <= 0
    ):
        raise ValueError("Size miss does not contain enough data for a corrected-bitrate retry.")
    corrected_bitrate = max(
        1_000,
        int(
            quality.selected_video_bitrate_bps
            * result.allowed_output_bytes
            / result.actual_output_bytes
            * 0.98
        ),
    )
    current_cap = item.options.max_video_kbps
    corrected_kbps = max(1, corrected_bitrate // 1_000)
    item.options.max_video_kbps = min(current_cap, corrected_kbps) if current_cap > 0 else corrected_kbps
    item.target_video_bitrate_bps = corrected_bitrate
    return corrected_bitrate
