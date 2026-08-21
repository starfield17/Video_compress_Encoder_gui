from __future__ import annotations

from pathlib import Path

from core.models import EncodePlanItem, PreviewJob, PreviewOptions, PreviewResult, PreviewSampleMode
from core.media.paths import preview_paths


def choose_sample_window(duration_sec: float, options: PreviewOptions) -> tuple[float, float, list[str]]:
    if duration_sec <= 0:
        raise ValueError("Source duration must be greater than 0.")

    notes: list[str] = []
    sample_duration = options.sample_duration_sec
    if sample_duration <= 0:
        raise ValueError("Preview sample duration must be greater than 0.")

    if sample_duration > duration_sec:
        notes.append("Sample duration exceeds source duration, using the full source instead.")
        sample_duration = duration_sec

    if options.sample_mode == PreviewSampleMode.CUSTOM:
        start_sec = float(options.custom_start_sec or 0.0)
        max_start = max(duration_sec - sample_duration, 0.0)
        clamped = min(max(start_sec, 0.0), max_start)
        if clamped != start_sec:
            notes.append("Custom preview start was out of range and has been clamped.")
        return clamped, sample_duration, notes

    # Default policy: sample from the middle for a representative segment.
    start_sec = max((duration_sec - sample_duration) / 2.0, 0.0)
    return start_sec, sample_duration, notes


def build_preview_job(
    plan_item: EncodePlanItem,
    workdir: Path,
    preview_options: PreviewOptions,
) -> PreviewJob:
    if not plan_item.media_info or not plan_item.encoder_info:
        raise ValueError("A preview job can only be built from a valid plan item.")

    start_sec, duration_sec, notes = choose_sample_window(
        plan_item.media_info.duration,
        preview_options,
    )
    source_sample_path, encoded_sample_path = preview_paths(
        workdir=workdir,
        source_path=plan_item.source_path,
        codec=plan_item.options.codec,
        container=plan_item.options.container,
    )
    return PreviewJob(
        source_path=plan_item.source_path,
        source_sample_path=source_sample_path,
        encoded_sample_path=encoded_sample_path,
        start_sec=start_sec,
        duration_sec=duration_sec,
        plan_item=plan_item,
        notes=notes,
    )


def estimate_preview(job: PreviewJob) -> PreviewResult:
    # Assumes the sample compression ratio applies uniformly to the full source.
    source_size = job.source_sample_path.stat().st_size
    encoded_size = job.encoded_sample_path.stat().st_size
    ratio = (encoded_size / source_size) if source_size > 0 else 0.0

    total_source_size = job.source_path.stat().st_size
    estimated_full_output_size = int(round(total_source_size * ratio))

    notes = list(job.notes)
    notes.append("Preview output size is estimated from the sample and is not an exact full-output guarantee.")

    return PreviewResult(
        job=job,
        success=True,
        source_sample_size=source_size,
        encoded_sample_size=encoded_size,
        sample_compression_ratio=ratio,
        estimated_full_output_size=estimated_full_output_size,
        notes=notes,
    )
