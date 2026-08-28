from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class SpaceSavingsOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_DECISION = "needs_decision"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SpaceSavingsItem:
    outcome: SpaceSavingsOutcome
    source_path: Path | None = None
    output_path: Path | None = None
    actual_output_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class SpaceSavingsSummary:
    total_files: int
    successful_files: int
    failed_files: int
    skipped_files: int
    needs_decision_files: int
    cancelled_files: int = 0
    original_total_bytes: int = 0
    compressed_total_bytes: int = 0
    saved_bytes: int = 0
    saved_ratio: float = 0.0
    total_elapsed_sec: float = 0.0


def _safe_file_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        pass
    return 0


def calculate_space_savings(
    records: Iterable[SpaceSavingsItem],
    total_elapsed_sec: float = 0.0,
) -> SpaceSavingsSummary:
    total_files = 0
    successful_files = 0
    failed_files = 0
    skipped_files = 0
    needs_decision_files = 0
    cancelled_files = 0

    original_total_bytes = 0
    compressed_total_bytes = 0

    for item in records:
        total_files += 1
        outcome = item.outcome

        if outcome == SpaceSavingsOutcome.SUCCESS:
            successful_files += 1
            source_size = _safe_file_size(item.source_path)

            output_size = 0
            if item.actual_output_bytes is not None and item.actual_output_bytes >= 0:
                output_size = int(item.actual_output_bytes)
            else:
                output_size = _safe_file_size(item.output_path)

            original_total_bytes += source_size
            compressed_total_bytes += output_size
        elif outcome == SpaceSavingsOutcome.FAILED:
            failed_files += 1
        elif outcome == SpaceSavingsOutcome.SKIPPED:
            skipped_files += 1
        elif outcome == SpaceSavingsOutcome.NEEDS_DECISION:
            needs_decision_files += 1
        elif outcome == SpaceSavingsOutcome.CANCELLED:
            cancelled_files += 1

    saved_bytes = original_total_bytes - compressed_total_bytes
    saved_ratio = (saved_bytes / original_total_bytes) if original_total_bytes > 0 else 0.0

    return SpaceSavingsSummary(
        total_files=total_files,
        successful_files=successful_files,
        failed_files=failed_files,
        skipped_files=skipped_files,
        needs_decision_files=needs_decision_files,
        cancelled_files=cancelled_files,
        original_total_bytes=original_total_bytes,
        compressed_total_bytes=compressed_total_bytes,
        saved_bytes=saved_bytes,
        saved_ratio=saved_ratio,
        total_elapsed_sec=max(0.0, float(total_elapsed_sec)),
    )
