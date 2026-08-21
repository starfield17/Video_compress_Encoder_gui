from __future__ import annotations

import math
import os
from dataclasses import replace
from pathlib import Path

from core.external_subtitles import copy_external_subtitles
from core.models import (
    AudioMode,
    ConstraintFailureKind,
    ConstraintPolicy,
    DecisionActionCode,
    DecisionOption,
    EncodeOptions,
    EncodePlanItem,
    EncodeResult,
    QualitySearchResult,
    SizeBlockedPolicy,
)
from core.smart_bitrate import reselect_from_candidates
from core.smart_cache import quality_configuration_fingerprint


def build_decision_options(result: QualitySearchResult) -> list[DecisionOption]:
    """Describe the valid next actions for an unsatisfied Smart result."""
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
        fingerprint=quality_configuration_fingerprint(
            ffmpeg_path,
            item,
            measurement_fingerprint=quality.measurement_fingerprint,
        ),
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
    corrected_bitrate = item.options.max_video_kbps * 1_000
    item.target_video_bitrate_bps = corrected_bitrate
    item.quality_search_result = None
    return corrected_bitrate
