"""Qt-free actions that mutate a queue record.

``QueueTableModel`` owns Qt notifications and row lookup.  This module owns
the domain side effects behind the queue's decision dialogs so the behavior is
testable without constructing a Qt model or view.
"""

from __future__ import annotations

from core.analysis_receipts import delete_analysis_receipt
from core.constraint_resolution import (
    accept_rejected_output,
    build_decision_options,
    discard_rejected_output,
    prepare_size_miss_retry,
    reselect_after_quality_decision,
)
from core.models import DecisionActionCode, DecisionOption, QualitySearchStatus
from gui.queue_state import QueueItemRecord, QueueItemStatus, reset_for_retry, short_error


def decision_options_for_record(record: QueueItemRecord) -> list[DecisionOption]:
    """Return local quality choices available for a needs-decision record."""

    if record.status != QueueItemStatus.NEEDS_DECISION:
        return []
    result = record.result
    if result is None or result.rejected_output_path is not None:
        return []
    quality = record.plan_item.quality_search_result
    return build_decision_options(quality) if quality is not None else []


def apply_quality_decision(record: QueueItemRecord, decision: DecisionOption) -> bool:
    """Apply a quality decision to ``record`` without emitting UI signals."""

    if record.status != QueueItemStatus.NEEDS_DECISION:
        return False
    quality = record.plan_item.quality_search_result
    if quality is None:
        return False

    if decision.action_code == DecisionActionCode.SKIP:
        if record.result is not None:
            record.result.needs_decision = False
            record.result.skipped = True
        record.status = QueueItemStatus.SKIPPED
        record.error_summary = quality.reason
        return True

    if decision.action_code == DecisionActionCode.REANALYZE:
        try:
            if quality.measurement_fingerprint:
                delete_analysis_receipt(record.job_snapshot.workdir, quality.measurement_fingerprint)
        except (OSError, ValueError) as exc:
            record.error_summary = short_error(str(exc))
            return False
        record.plan_item.quality_search_result = None
        reset_for_retry(record)
        return True

    reselected = reselect_after_quality_decision(
        record.job_snapshot.ffmpeg_path,
        record.plan_item,
        quality,
        decision,
    )
    record.plan_item.quality_search_result = reselected
    if reselected.status == QualitySearchStatus.FOUND:
        record.plan_item.target_video_bitrate_bps = reselected.selected_video_bitrate_bps
        reset_for_retry(record)
    elif decision.requires_analysis:
        reselected.fingerprint = ""
        reset_for_retry(record)
    else:
        if record.result is not None:
            record.result.quality_search_result = reselected
            record.result.error_message = reselected.reason
        record.error_summary = reselected.reason
    return True


def _size_miss_record(record: QueueItemRecord) -> bool:
    result = record.result
    return (
        record.status == QueueItemStatus.NEEDS_DECISION
        and result is not None
        and result.rejected_output_path is not None
    )


def accept_size_miss(record: QueueItemRecord) -> bool:
    """Publish the preserved output for a size miss."""

    if not _size_miss_record(record):
        return False
    result = record.result
    assert result is not None
    try:
        accept_rejected_output(record.plan_item, result)
    except (OSError, ValueError) as exc:
        record.error_summary = short_error(str(exc))
        return False
    record.status = QueueItemStatus.DONE
    record.file_progress = 100.0
    record.error_summary = None
    return True


def discard_size_miss(record: QueueItemRecord) -> bool:
    """Delete the preserved output and mark the item skipped."""

    if not _size_miss_record(record):
        return False
    result = record.result
    assert result is not None
    try:
        discard_rejected_output(record.plan_item, result)
    except (OSError, ValueError) as exc:
        record.error_summary = short_error(str(exc))
        return False
    record.status = QueueItemStatus.SKIPPED
    record.error_summary = result.error_message
    return True


def retry_size_miss(record: QueueItemRecord) -> bool:
    """Prepare a preserved size miss for another encode attempt."""

    if not _size_miss_record(record):
        return False
    result = record.result
    assert result is not None
    try:
        prepare_size_miss_retry(record.plan_item, result)
    except ValueError as exc:
        record.error_summary = short_error(str(exc))
        return False
    reset_for_retry(record)
    return True
