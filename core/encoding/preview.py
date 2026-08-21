"""Preview extraction and Smart preview execution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from core.ffmpeg.commands import build_preview_encode_commands, build_preview_extract_command
from core.media.paths import log_file_path
from core.media.preview import estimate_preview
from core.media.validation import validate_workdir
from core.models import (
    EncodePlanItem,
    OperationCancelledError,
    PreviewJob,
    PreviewResult,
    QualitySearchResult,
    QualitySearchStatus,
    SmartPreviewResult,
)
from core.progress_events import ProgressCallback
from core.smart.concurrency import analysis_slot
from core.smart.workflow import analyze_quality

from .process import _cleanup_passlog, _emit, _emit_progress, _run_logged_command


def execute_preview(
    job: PreviewJob,
    ffmpeg_path: Path,
    workdir: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> PreviewResult:
    # Two-phase pipeline: extract a sample, then encode it with planned settings.
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, job.source_path, "preview")
    extract_cmd = build_preview_extract_command(ffmpeg_path, job)
    encode_cmds, passlog = build_preview_encode_commands(ffmpeg_path, job, workdir)
    try:
        _emit(log_callback, f"Preview extraction started for {job.source_path.name}")
        _emit_progress(
            progress_callback,
            stage="preview",
            state="extracting",
            file_name=job.source_path.name,
            file_path=str(job.source_path),
            output_path=str(job.source_sample_path),
            percent=0.0,
            duration_sec=job.duration_sec,
        )
        _run_logged_command(
            extract_cmd,
            log_path,
            log_callback,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            process_callback=process_callback,
            progress_context={
                "stage": "preview",
                "phase": "extract",
                "file_name": job.source_path.name,
                "file_path": str(job.source_path),
                "output_path": str(job.source_sample_path),
                "duration_sec": job.duration_sec,
            },
        )
        _emit(log_callback, f"Preview encode started for {job.source_path.name}")
        _emit_progress(
            progress_callback,
            stage="preview",
            state="encoding",
            file_name=job.source_path.name,
            file_path=str(job.source_path),
            output_path=str(job.encoded_sample_path),
            percent=0.0,
            duration_sec=job.duration_sec,
        )
        for cmd in encode_cmds:
            _run_logged_command(
                cmd,
                log_path,
                log_callback,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                process_callback=process_callback,
                progress_context={
                    "stage": "preview",
                    "phase": "encode",
                    "file_name": job.source_path.name,
                    "file_path": str(job.source_path),
                    "output_path": str(job.encoded_sample_path),
                    "duration_sec": job.duration_sec,
                },
            )
        result = estimate_preview(job)
        result.log_path = log_path
        _emit(log_callback, f"Preview finished for {job.source_path.name}")
        _emit_progress(
            progress_callback,
            stage="preview",
            state="finished",
            file_name=job.source_path.name,
            file_path=str(job.source_path),
            output_path=str(job.encoded_sample_path),
            percent=100.0,
        )
        return result
    except OperationCancelledError:
        _emit(log_callback, f"Preview cancelled for {job.source_path.name}")
        _emit_progress(
            progress_callback,
            stage="preview",
            state="cancelled",
            file_name=job.source_path.name,
            file_path=str(job.source_path),
            output_path=str(job.encoded_sample_path),
        )
        raise
    except subprocess.CalledProcessError as exc:
        _emit(log_callback, f"Preview failed for {job.source_path.name} (exit code {exc.returncode})")
        return PreviewResult(
            job=job,
            success=False,
            notes=list(job.notes),
            log_path=log_path,
            error_message=exc.stderr or exc.stdout or str(exc),
        )
    finally:
        _cleanup_passlog(passlog)


def execute_smart_preview(
    item: EncodePlanItem,
    ffmpeg_path: Path,
    workdir: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> SmartPreviewResult:
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, item.source_path, "smart-preview")
    try:
        with analysis_slot(cancel_check):
            quality_result = analyze_quality(
                ffmpeg_path,
                item,
                workdir,
                log_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                process_callback=process_callback,
            )
        _emit(log_callback, f"Smart preview finished for {item.source_path.name}")
        return SmartPreviewResult(
            source_path=item.source_path,
            success=quality_result.success,
            quality_search_result=quality_result,
            log_path=log_path,
            error_message=None if quality_result.success else quality_result.reason,
        )
    except OperationCancelledError:
        raise
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as exc:
        encoder = item.encoder_info
        failed_result = item.quality_search_result
        if failed_result is None:
            failed_result = QualitySearchResult(
                status=QualitySearchStatus.FAILED,
                encoder_name=encoder.encoder_name if encoder else "",
                backend=encoder.backend if encoder else item.options.backend,
                reason=str(exc),
            )
        return SmartPreviewResult(
            source_path=item.source_path,
            success=False,
            quality_search_result=failed_result,
            log_path=log_path,
            error_message=str(exc),
        )
