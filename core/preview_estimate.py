from __future__ import annotations

from core.models import PreviewJob, PreviewResult


def estimate_preview(job: PreviewJob) -> PreviewResult:
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
