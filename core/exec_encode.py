from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from core.build_ffmpeg_cmd import (
    build_encode_commands,
    build_preview_encode_commands,
    build_preview_extract_command,
)
from core.models import EncodePlan, EncodeResult, PreviewJob, PreviewResult
from core.path_utils import log_file_path
from core.preview_estimate import estimate_preview
from core.safety_checks import validate_workdir


def _emit(log_callback: Callable[[str], None] | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)


def _run_logged_command(
    cmd: list[str],
    log_path: Path,
    log_callback: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    with log_path.open("a", encoding="utf-8") as log_file:
        command_line = "$ " + " ".join(cmd)
        log_file.write(command_line + "\n")
        log_file.flush()
        _emit(log_callback, command_line)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        output_chunks: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            normalized = line.rstrip("\r\n")
            output_chunks.append(line)
            log_file.write(line)
            log_file.flush()
            if normalized:
                _emit(log_callback, normalized)
        return_code = proc.wait()
        log_file.write("\n")
        log_file.flush()

    stdout_text = "".join(output_chunks)
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
) -> list[EncodeResult]:
    workdir = validate_workdir(workdir)
    results: list[EncodeResult] = []

    _emit(log_callback, "Encode execution started.")
    for index, item in enumerate(plan.items, start=1):
        log_path = log_file_path(workdir, item.source_path, "encode")
        if item.skip_reason:
            _emit(
                log_callback,
                f"[{index}/{len(plan.items)}] Skipping {item.source_path.name}: {item.skip_reason}",
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
        result = EncodeResult(
            source_path=item.source_path,
            output_path=item.output_path,
            success=True,
            commands=commands,
            log_path=log_path,
        )
        try:
            for cmd in commands:
                _run_logged_command(cmd, log_path, log_callback)
            _emit(
                log_callback,
                f"[{index}/{len(plan.items)}] Finished {item.source_path.name}",
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
        finally:
            _cleanup_passlog(passlog)
        results.append(result)
    _emit(log_callback, "Encode execution finished.")
    return results


def execute_preview(
    job: PreviewJob,
    ffmpeg_path: Path,
    workdir: Path,
    log_callback: Callable[[str], None] | None = None,
) -> PreviewResult:
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, job.source_path, "preview")
    extract_cmd = build_preview_extract_command(ffmpeg_path, job)
    encode_cmds, passlog = build_preview_encode_commands(ffmpeg_path, job, workdir)

    try:
        _emit(log_callback, f"Preview extraction started for {job.source_path.name}")
        _run_logged_command(extract_cmd, log_path, log_callback)
        _emit(log_callback, f"Preview encode started for {job.source_path.name}")
        for cmd in encode_cmds:
            _run_logged_command(cmd, log_path, log_callback)
        result = estimate_preview(job)
        result.log_path = log_path
        _emit(log_callback, f"Preview finished for {job.source_path.name}")
        return result
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
