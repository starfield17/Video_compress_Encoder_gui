from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from core.external_subtitles import copy_external_subtitles
from core.models import EncodePlanItem, EncodeResult, SkippedOutputPolicy
from core.path_utils import ensure_dir


@dataclass(frozen=True, slots=True)
class SkippedPublishResult:
    source_path: Path
    output_path: Path
    copied: bool
    reason: str | None = None


def is_eligible_skipped_item(item: EncodePlanItem, result: EncodeResult) -> bool:
    if not result.skipped or result.needs_decision:
        return False
    if item.skip_reason:
        return False
    try:
        return item.source_path.is_file()
    except OSError:
        return False


def publish_skipped_source(item: EncodePlanItem) -> SkippedPublishResult:
    source = item.source_path
    destination = item.output_path
    if not source.is_file():
        return SkippedPublishResult(source, destination, False, "source is missing")
    try:
        if destination.exists() and not item.options.overwrite:
            return SkippedPublishResult(destination, destination, False, "output exists")
        if source.resolve() == destination.resolve():
            return SkippedPublishResult(source, destination, False, "source and output are the same file")
        ensure_dir(destination.parent)
        shutil.copy2(source, destination)
    except OSError as exc:
        return SkippedPublishResult(source, destination, False, str(exc))
    if item.options.copy_external_subtitles:
        copy_external_subtitles(source, destination, overwrite=item.options.overwrite)
    return SkippedPublishResult(source, destination, True)


def publish_skipped_sources(
    pairs: list[tuple[EncodePlanItem, EncodeResult]],
) -> list[SkippedPublishResult]:
    published: list[SkippedPublishResult] = []
    for item, result in pairs:
        if is_eligible_skipped_item(item, result):
            published.append(publish_skipped_source(item))
    return published


def group_skipped_output_pairs(
    pairs: list[tuple[EncodePlanItem, EncodeResult]],
) -> dict[SkippedOutputPolicy, list[tuple[EncodePlanItem, EncodeResult]]]:
    grouped = {policy: [] for policy in SkippedOutputPolicy}
    for item, result in pairs:
        if is_eligible_skipped_item(item, result):
            grouped[item.options.skipped_output_policy].append((item, result))
    return grouped
