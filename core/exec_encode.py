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
                        _emit_progress(progress_callback, category="ffmpeg", message=normalized, **(progress_context or {}), **parsed)
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
        log_path = log_file_path(workdir, item.source_path, "encode")
        if item.skip_reason:
            _emit(
                log_callback,
                f"[{index}/{len(plan.items)}] Skipping {item.source_path.name}: {item.skip_reason}",
            )
            _emit_progress(
                progress_callback,
                stage="encode",
                state="skipped",
                file_name=item.source_path.name,
                file_path=str(item.source_path),
                current=index,
                total=len(plan.items),
                percent=(index / max(len(plan.items), 1)) * 100.0,
                message=item.skip_reason,
            )
            results.append(
                EncodeResult(
                    source_path=item.source_path,
                    output_path=item.output_path,
                    success=False,
                    skipped=True,
                    error_message=item.skip_reason,
                    log_path=log_path,
                )
            )
            continue

        commands, passlog = build_encode_commands(plan.ffmpeg_path, item, workdir)
        _emit(
            log_callback,
            f"[{index}/{len(plan.items)}] Encoding {item.source_path.name} -> {item.output_path}",
        )
        _emit_progress(
            progress_callback,
            stage="encode",
            state="starting_file",
            file_name=item.source_path.name,
            file_path=str(item.source_path),
            output_path=str(item.output_path),
            current=index,
            total=len(plan.items),
            percent=((index - 1) / max(len(plan.items), 1)) * 100.0,
        )
        result = EncodeResult(
            source_path=item.source_path,
            output_path=item.output_path,
            success=True,
            commands=commands,
            log_path=log_path,
        )
        try:
            for cmd in commands:
                _run_logged_command(
                    cmd,
                    log_path,
                    log_callback,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    process_callback=process_callback,
                    progress_context={
                        "stage": "encode",
                        "file_name": item.source_path.name,
                        "file_path": str(item.source_path),
                        "output_path": str(item.output_path),
                        "current": index,
                        "total": len(plan.items),
                        "duration_sec": item.media_info.duration if item.media_info else None,
                    },
                )
            _emit(
                log_callback,
                f"[{index}/{len(plan.items)}] Finished {item.source_path.name}",
            )
            _emit_progress(
                progress_callback,
                stage="encode",
                state="finished_file",
                file_name=item.source_path.name,
                file_path=str(item.source_path),
                output_path=str(item.output_path),
                current=index,
                total=len(plan.items),
                percent=(index / max(len(plan.items), 1)) * 100.0,
            )
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
                f"[{index}/{len(plan.items)}] Failed {item.source_path.name} (exit code {exc.returncode})",
            )
            _emit_progress(
                progress_callback,
                stage="encode",
                state="failed_file",
                file_name=item.source_path.name,
                file_path=str(item.source_path),
                output_path=str(item.output_path),
                current=index,
                total=len(plan.items),
                percent=(index / max(len(plan.items), 1)) * 100.0,
                message=result.error_message or "",
            )
        finally:
            _cleanup_passlog(passlog)
        results.append(result)
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
