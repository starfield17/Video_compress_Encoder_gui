from __future__ import annotations

import copy
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.models import EncodePlan, EncodePlanItem, EncodeResult, MediaInfo


class QueueItemStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


STATUS_KEY_BY_VALUE = {
    QueueItemStatus.DRAFT: "gui.status.draft",
    QueueItemStatus.QUEUED: "gui.status.ready",
    QueueItemStatus.RUNNING: "gui.status.running",
    QueueItemStatus.PAUSED: "gui.status.paused",
    QueueItemStatus.DONE: "gui.status.done",
    QueueItemStatus.FAILED: "gui.status.failed",
    QueueItemStatus.SKIPPED: "gui.status.skip",
    QueueItemStatus.CANCELLED: "gui.status.cancelled",
}


@dataclass(slots=True)
class QueueJobSnapshot:
    workdir: Path
    ffmpeg_path: Path
    ffprobe_path: Path
    output_root: Path


@dataclass(slots=True)
class QueueItemRecord:
    item_id: str
    plan_item: EncodePlanItem
    job_snapshot: QueueJobSnapshot
    status: QueueItemStatus
    total_passes: int
    current_pass_index: int = 0
    pass_percent: float = 0.0
    file_progress: float = 0.0
    error_summary: str | None = None
    log_path: Path | None = None
    last_speed: str = ""
    elapsed_sec: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    assigned_backend: str | None = None
    assigned_encoder: str | None = None
    result: EncodeResult | None = None

    @property
    def source_path(self) -> Path:
        return self.plan_item.source_path

    @property
    def output_path(self) -> Path:
        return self.plan_item.output_path

    @property
    def media_info(self) -> MediaInfo | None:
        return self.plan_item.media_info

    @property
    def duration_sec(self) -> float:
        if self.plan_item.media_info is None:
            return 0.0
        return float(self.plan_item.media_info.duration or 0.0)

    @property
    def effective_weight(self) -> float:
        return self.duration_sec * max(self.total_passes, 1)


@dataclass(slots=True)
class QueueMetrics:
    total_items: int = 0
    queued_items: int = 0
    running_items: int = 0
    failed_items: int = 0
    done_items: int = 0
    skipped_items: int = 0
    cancelled_items: int = 0
    ready_items: int = 0
    total_duration_sec: float = 0.0
    queue_percent: float = 0.0
    eta_sec: float | None = None
    completed_items: int = 0
    estimated_saved_bytes: int | None = None
    current_item_id: str | None = None
    current_file_name: str = "-"
    current_file_percent: float | None = None
    current_speed: str = "-"
    current_elapsed_sec: float | None = None
    current_total_duration_sec: float | None = None


def build_queue_job_snapshot(plan: EncodePlan, workdir: Path) -> QueueJobSnapshot:
    return QueueJobSnapshot(
        workdir=workdir,
        ffmpeg_path=plan.ffmpeg_path,
        ffprobe_path=plan.ffprobe_path,
        output_root=plan.output_root,
    )


def clone_plan_item(plan_item: EncodePlanItem) -> EncodePlanItem:
    return copy.deepcopy(plan_item)


def create_queue_records(plan: EncodePlan, workdir: Path) -> list[QueueItemRecord]:
    job_snapshot = build_queue_job_snapshot(plan, workdir)
    records: list[QueueItemRecord] = []
    for item in plan.items:
        total_passes = 2 if item.options.two_pass and item.encoder_info and item.encoder_info.supports_two_pass else 1
        status = QueueItemStatus.SKIPPED if item.skip_reason else QueueItemStatus.QUEUED
        file_progress = 100.0 if status == QueueItemStatus.SKIPPED else 0.0
        records.append(
            QueueItemRecord(
                item_id=uuid.uuid4().hex,
                plan_item=clone_plan_item(item),
                job_snapshot=job_snapshot,
                status=status,
                total_passes=total_passes,
                current_pass_index=0 if status != QueueItemStatus.SKIPPED else total_passes,
                pass_percent=100.0 if status == QueueItemStatus.SKIPPED else 0.0,
                file_progress=file_progress,
                error_summary=item.skip_reason,
            )
        )
    return records


def status_key(status: QueueItemStatus) -> str:
    return STATUS_KEY_BY_VALUE[status]


def short_error(message: str | None, limit: int = 120) -> str:
    if not message:
        return ""
    normalized = " ".join(message.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def build_tags(record: QueueItemRecord) -> list[str]:
    tags: list[str] = []
    if record.total_passes > 1:
        tags.append("Two-pass")
    if record.plan_item.options.overwrite:
        tags.append("Overwrite")
    if record.plan_item.options.copy_external_subtitles:
        tags.append("ExtSub")
    if record.plan_item.warnings:
        tags.append("Warn")
    if record.status == QueueItemStatus.SKIPPED:
        tags.append("Skip")
    if record.status == QueueItemStatus.FAILED:
        tags.append("Fail")
    if record.error_summary and record.status not in {QueueItemStatus.SKIPPED, QueueItemStatus.FAILED}:
        tags.append("Note")
    return tags


def build_tooltip(record: QueueItemRecord) -> str:
    lines = [
        f"Source: {record.source_path}",
        f"Output: {record.output_path}",
    ]
    if record.plan_item.warnings:
        lines.append("Warnings: " + "; ".join(record.plan_item.warnings))
    if record.error_summary:
        lines.append("Detail: " + record.error_summary)
    return "\n".join(lines)


def parse_speed_factor(speed: str) -> float | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)x\s*", speed)
    if not match:
        return None
    value = float(match.group(1))
    if value <= 0:
        return None
    return value


def parse_bitrate_to_bps(raw: str) -> int | None:
    value = raw.strip().lower()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kmg]?)", value)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    scale = {"": 1, "k": 1000, "m": 1000_000, "g": 1000_000_000}[suffix]
    return int(number * scale)


def estimate_saved_bytes(records: list[QueueItemRecord]) -> int | None:
    total_saved = 0
    has_estimate = False
    for record in records:
        media = record.media_info
        if media is None or media.duration <= 0 or record.plan_item.target_video_bitrate_bps <= 0:
            continue

        if record.plan_item.options.audio_mode.value == "copy":
            audio_bitrate_bps = max(int(media.audio_bitrate_bps or 0), 0)
        else:
            parsed_audio = parse_bitrate_to_bps(record.plan_item.options.audio_bitrate)
            audio_bitrate_bps = max(parsed_audio or 0, 0)
        estimated_output_bytes = int(media.duration * (record.plan_item.target_video_bitrate_bps + audio_bitrate_bps) / 8.0)

        try:
            source_bytes = record.source_path.stat().st_size
        except OSError:
            source_bytes = int(media.duration * max(int(media.format_bitrate_bps or 0), 0) / 8.0)

        if source_bytes <= 0 or estimated_output_bytes <= 0:
            continue
        total_saved += source_bytes - estimated_output_bytes
        has_estimate = True
    return total_saved if has_estimate else None


def processed_weight(record: QueueItemRecord) -> float:
    weight = record.effective_weight
    if weight <= 0:
        return 0.0
    if record.status in {QueueItemStatus.DONE, QueueItemStatus.SKIPPED}:
        return weight
    return weight * max(0.0, min(100.0, record.file_progress)) / 100.0


def compute_metrics(records: list[QueueItemRecord]) -> QueueMetrics:
    metrics = QueueMetrics()
    metrics.total_items = len(records)
    metrics.total_duration_sec = sum(record.duration_sec for record in records if record.duration_sec > 0)
    metrics.estimated_saved_bytes = estimate_saved_bytes(records)

    total_weight = 0.0
    completed_weight = 0.0
    running_record: QueueItemRecord | None = None
    for record in records:
        weight = record.effective_weight
        total_weight += weight
        completed_weight += processed_weight(record)
        if record.status == QueueItemStatus.QUEUED:
            metrics.queued_items += 1
        elif record.status == QueueItemStatus.RUNNING:
            metrics.running_items += 1
            running_record = record
        elif record.status == QueueItemStatus.FAILED:
            metrics.failed_items += 1
        elif record.status == QueueItemStatus.DONE:
            metrics.done_items += 1
        elif record.status == QueueItemStatus.SKIPPED:
            metrics.skipped_items += 1
        elif record.status == QueueItemStatus.CANCELLED:
            metrics.cancelled_items += 1

    metrics.ready_items = metrics.queued_items
    metrics.completed_items = metrics.done_items + metrics.skipped_items
    if total_weight > 0:
        metrics.queue_percent = max(0.0, min(100.0, (completed_weight / total_weight) * 100.0))

    if running_record is not None:
        metrics.current_item_id = running_record.item_id
        metrics.current_file_name = running_record.source_path.name
        metrics.current_file_percent = running_record.file_progress
        metrics.current_speed = running_record.last_speed or "-"
        metrics.current_elapsed_sec = running_record.elapsed_sec
        metrics.current_total_duration_sec = running_record.duration_sec or None
        speed_factor = parse_speed_factor(running_record.last_speed or "")
        if speed_factor:
            remaining_weight = max(0.0, total_weight - completed_weight)
            metrics.eta_sec = remaining_weight / speed_factor
    return metrics


def mark_started(record: QueueItemRecord) -> None:
    record.status = QueueItemStatus.RUNNING
    record.started_at = time.time()
    record.finished_at = None
    record.error_summary = None
    record.result = None
    record.log_path = None
    record.current_pass_index = 1
    record.pass_percent = 0.0
    record.file_progress = 0.0


def reset_for_retry(record: QueueItemRecord) -> None:
    record.status = QueueItemStatus.QUEUED
    record.current_pass_index = 0
    record.pass_percent = 0.0
    record.file_progress = 0.0
    record.error_summary = None
    record.log_path = None
    record.last_speed = ""
    record.elapsed_sec = None
    record.started_at = None
    record.finished_at = None
    record.assigned_backend = None
    record.assigned_encoder = None
    record.result = None


def mark_finished(record: QueueItemRecord, result: EncodeResult) -> None:
    record.result = result
    record.log_path = result.log_path
    record.finished_at = time.time()
    if result.skipped:
        record.status = QueueItemStatus.SKIPPED
        record.file_progress = 100.0
        record.error_summary = short_error(result.error_message)
        return
    if result.success:
        record.status = QueueItemStatus.DONE
        record.file_progress = 100.0
        record.pass_percent = 100.0
        record.current_pass_index = max(record.total_passes, 1)
        record.error_summary = None
        return
    record.status = QueueItemStatus.FAILED
    record.error_summary = short_error(result.error_message)


def assign_runtime_backend(record: QueueItemRecord, backend: str | None, encoder: str | None) -> None:
    record.assigned_backend = backend
    record.assigned_encoder = encoder


def mark_cancelled(record: QueueItemRecord, message: str | None = None) -> None:
    record.status = QueueItemStatus.CANCELLED
    record.finished_at = time.time()
    record.error_summary = short_error(message)


def mark_failed(record: QueueItemRecord, message: str | None = None) -> None:
    record.status = QueueItemStatus.FAILED
    record.finished_at = time.time()
    record.error_summary = short_error(message)
