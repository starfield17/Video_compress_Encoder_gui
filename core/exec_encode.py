from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

from core.build_ffmpeg_cmd import (
    build_encode_commands,
    build_preview_encode_commands,
    build_preview_extract_command,
)
from core.external_subtitles import copy_external_subtitles
from core.models import (
    EncodePlan,
    EncodeResult,
    OperationCancelledError,
    PreviewJob,
    PreviewResult,
)
from core.path_utils import log_file_path
from core.preview_estimate import estimate_preview
from core.safety_checks import validate_workdir


def _emit(log_callback: Callable[[str], None] | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)


def _emit_progress(
    progress_callback: Callable[[dict[str, object]], None] | None,
    **event: object,
) -> None:
    if progress_callback is not None:
        progress_callback(event)


def _parse_time_to_seconds(raw: str) -> float | None:
    try:
        hours, minutes, seconds = raw.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def _parse_ffmpeg_progress(line: str, duration_sec: float | None) -> dict[str, object] | None:
    if "time=" not in line and "speed=" not in line:
        return None

    match_time = re.search(r"time=(\d+:\d+:\d+(?:\.\d+)?)", line)
    match_speed = re.search(r"speed=\s*([0-9.]+x)", line)
    match_frame = re.search(r"frame=\s*(\d+)", line)
    elapsed_sec = _parse_time_to_seconds(match_time.group(1)) if match_time else None
    percent = None
    if duration_sec and elapsed_sec is not None and duration_sec > 0:
        percent = max(0.0, min(100.0, (elapsed_sec / duration_sec) * 100.0))

    event: dict[str, object] = {
        "state": "running",
        "elapsed_sec": elapsed_sec,
        "percent": percent,
        "speed": match_speed.group(1) if match_speed else "",
        "frame": int(match_frame.group(1)) if match_frame else None,
    }
    return event


def _cancel_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_logged_command(
    cmd: list[str],
    log_path: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
    progress_context: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    with log_path.open("a", encoding="utf-8") as log_file:
        command_line = "$ " + " ".join(cmd)
        log_file.write(command_line + "\n")
        log_file.flush()
        _emit(log_callback, command_line)
        _emit_progress(progress_callback, category="command", message=command_line, **(progress_context or {}))

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if process_callback is not None:
            process_callback(proc)
        output_chunks: list[str] = []
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if cancel_check is not None and cancel_check():
                    _emit(log_callback, "Cancellation requested. Stopping ffmpeg...")
                    _emit_progress(
                        progress_callback,
                        category="status",
                        state="cancelling",
                        message="Cancellation requested. Stopping ffmpeg...",
                        **(progress_context or {}),
                    )
                    _cancel_process(proc)
                    raise OperationCancelledError("Operation cancelled.")

                normalized = line.rstrip("\r\n")
                output_chunks.append(line)
                log_file.write(line)
                log_file.flush()
                if normalized:
                    _emit(log_callback, normalized)
                    parsed = _parse_ffmpeg_progress(
                        normalized,
                        progress_context.get("duration_sec") if progress_context else None,
                    )
                    if parsed is not None:
                        event = dict(parsed)
                        if progress_context:
                            current_pass_index = progress_context.get("current_pass_index")
                            total_passes = progress_context.get("total_passes")
                            if isinstance(current_pass_index, int) and isinstance(total_passes, int) and total_passes > 0:
                                pass_percent = float(event.get("percent") or 0.0)
                                file_progress = (((current_pass_index - 1) + (pass_percent / 100.0)) / total_passes) * 100.0
                                event["pass_percent"] = pass_percent
                                event["file_progress"] = max(0.0, min(100.0, file_progress))
                                event["percent"] = event["file_progress"]
                        _emit_progress(progress_callback, category="ffmpeg", message=normalized, **(progress_context or {}), **event)
                    else:
                        _emit_progress(progress_callback, category="log", message=normalized, **(progress_context or {}))
            return_code = proc.wait()
            log_file.write("\n")
            log_file.flush()
        finally:
            if process_callback is not None:
                process_callback(None)

    stdout_text = "".join(output_chunks)
    if cancel_check is not None and cancel_check():
        raise OperationCancelledError("Operation cancelled.")
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd, output=stdout_text)
    return subprocess.CompletedProcess(
        cmd,
        return_code,
        stdout=stdout_text,
        stderr="",
    )


def execute_plan_item(
    ffmpeg_path: Path,
    item,
    workdir: Path,
    *,
    queue_index: int = 1,
    queue_total: int = 1,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
    extra_progress_context: dict[str, object] | None = None,
) -> EncodeResult:
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, item.source_path, "encode")
    base_context = {
        "stage": "encode",
        "file_name": item.source_path.name,
        "file_path": str(item.source_path),
        "output_path": str(item.output_path),
        "current": queue_index,
        "total": queue_total,
        "duration_sec": item.media_info.duration if item.media_info else None,
    }
    if extra_progress_context:
        base_context.update(extra_progress_context)

    if item.skip_reason:
        _emit(
            log_callback,
            f"[{queue_index}/{queue_total}] Skipping {item.source_path.name}: {item.skip_reason}",
        )
        _emit_progress(
            progress_callback,
            state="skipped",
            percent=100.0,
            pass_percent=100.0,
            file_progress=100.0,
            current_pass_index=0,
            total_passes=0,
            message=item.skip_reason,
            **base_context,
        )
        return EncodeResult(
            source_path=item.source_path,
            output_path=item.output_path,
            success=False,
            skipped=True,
            error_message=item.skip_reason,
            log_path=log_path,
        )

    commands, passlog = build_encode_commands(ffmpeg_path, item, workdir)
    total_passes = max(len(commands), 1)
    _emit(
        log_callback,
        f"[{queue_index}/{queue_total}] Encoding {item.source_path.name} -> {item.output_path}",
    )
    _emit_progress(
        progress_callback,
        state="starting_file",
        percent=0.0,
        pass_percent=0.0,
        file_progress=0.0,
        current_pass_index=1,
        total_passes=total_passes,
        **base_context,
    )
    result = EncodeResult(
        source_path=item.source_path,
        output_path=item.output_path,
        success=True,
        commands=commands,
        log_path=log_path,
    )
    current_pass_index = 1
    try:
        for pass_index, cmd in enumerate(commands, start=1):
            current_pass_index = pass_index
            file_progress = ((pass_index - 1) / total_passes) * 100.0
            _emit_progress(
                progress_callback,
                state="running_pass",
                percent=file_progress,
                pass_percent=0.0,
                file_progress=file_progress,
                current_pass_index=pass_index,
                total_passes=total_passes,
                **base_context,
            )
            _run_logged_command(
                cmd,
                log_path,
                log_callback,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                process_callback=process_callback,
                progress_context={
                    **base_context,
                    "current_pass_index": pass_index,
                    "total_passes": total_passes,
                },
            )
        if item.options.copy_external_subtitles:
            copied_paths, warnings = copy_external_subtitles(
                item.source_path,
                item.output_path,
                overwrite=item.options.overwrite,
            )
            result.copied_external_subtitle_paths.extend(copied_paths)
            result.external_subtitle_warnings.extend(warnings)
            for copied_path in copied_paths:
                _emit(
                    log_callback,
                    f"[{queue_index}/{queue_total}] Copied external subtitle -> {copied_path}",
                )
            for warning in warnings:
                _emit(
                    log_callback,
                    f"[{queue_index}/{queue_total}] External subtitle warning: {warning}",
                )
        _emit(
            log_callback,
            f"[{queue_index}/{queue_total}] Finished {item.source_path.name}",
        )
        _emit_progress(
            progress_callback,
            state="finished_file",
            percent=100.0,
            pass_percent=100.0,
            file_progress=100.0,
            current_pass_index=total_passes,
            total_passes=total_passes,
            **base_context,
        )
        return result
    except OperationCancelledError:
        _emit_progress(
            progress_callback,
            state="cancelled_file",
            percent=None,
            current_pass_index=current_pass_index,
            total_passes=total_passes,
            **base_context,
        )
        raise
    except subprocess.CalledProcessError as exc:
        result.success = False
        result.return_code = exc.returncode
        result.error_message = exc.stderr or exc.stdout or str(exc)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[command failed] returncode={exc.returncode}\n")
            if exc.stdout:
                fh.write(exc.stdout + "\n")
            if exc.stderr:
                fh.write(exc.stderr + "\n")
        _emit(
            log_callback,
            f"[{queue_index}/{queue_total}] Failed {item.source_path.name} (exit code {exc.returncode})",
        )
        _emit_progress(
            progress_callback,
            state="failed_file",
            message=result.error_message or "",
            current_pass_index=total_passes,
            total_passes=total_passes,
            **base_context,
        )
        return result
    finally:
        _cleanup_passlog(passlog)


def _cleanup_passlog(passlog: Path | None) -> None:
    if not passlog:
        return
    for candidate in passlog.parent.glob(passlog.name + "*"):
        try:
            candidate.unlink()
        except OSError:
            pass


def execute_plan(
    plan: EncodePlan,
    workdir: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> list[EncodeResult]:
    workdir = validate_workdir(workdir)
    results: list[EncodeResult] = []

    _emit(log_callback, "Encode execution started.")
    _emit_progress(progress_callback, stage="encode", state="started", percent=0.0)
    for index, item in enumerate(plan.items, start=1):
        if cancel_check is not None and cancel_check():
            _emit(log_callback, "Encode execution cancelled by user.")
            _emit_progress(progress_callback, stage="encode", state="cancelled")
            raise OperationCancelledError("Encoding cancelled.")
        results.append(
            execute_plan_item(
                plan.ffmpeg_path,
                item,
                workdir,
                queue_index=index,
                queue_total=len(plan.items),
                log_callback=log_callback,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                process_callback=process_callback,
            )
        )
    _emit(log_callback, "Encode execution finished.")
    _emit_progress(progress_callback, stage="encode", state="finished", percent=100.0)
    return results


def execute_preview(
    job: PreviewJob,
    ffmpeg_path: Path,
    workdir: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> PreviewResult:
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
        _emit(
            log_callback,
            f"Preview failed for {job.source_path.name} (exit code {exc.returncode})",
        )
        return PreviewResult(
            job=job,
            success=False,
            notes=list(job.notes),
            log_path=log_path,
            error_message=exc.stderr or exc.stdout or str(exc),
        )
    finally:
        _cleanup_passlog(passlog)
