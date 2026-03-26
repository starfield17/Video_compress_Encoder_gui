from __future__ import annotations

import subprocess
from pathlib import Path

from core.build_ffmpeg_cmd import (
    build_encode_commands,
    build_preview_encode_commands,
    build_preview_extract_command,
)
from core.models import EncodePlan, EncodeResult, PreviewJob, PreviewResult
from core.path_utils import log_file_path
from core.preview_estimate import estimate_preview
from core.safety_checks import validate_workdir


def _run_logged_command(cmd: list[str], log_path: Path) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        if proc.stdout:
            fh.write(proc.stdout)
        if proc.stderr:
            fh.write(proc.stderr)
        fh.write("\n")
    return proc


def _cleanup_passlog(passlog: Path | None) -> None:
    if not passlog:
        return
    for candidate in passlog.parent.glob(passlog.name + "*"):
        try:
            candidate.unlink()
        except OSError:
            pass


def execute_plan(plan: EncodePlan, workdir: Path) -> list[EncodeResult]:
    workdir = validate_workdir(workdir)
    results: list[EncodeResult] = []

    for item in plan.items:
        log_path = log_file_path(workdir, item.source_path, "encode")
        if item.skip_reason:
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
        result = EncodeResult(
            source_path=item.source_path,
            output_path=item.output_path,
            success=True,
            commands=commands,
            log_path=log_path,
        )
        try:
            for cmd in commands:
                _run_logged_command(cmd, log_path)
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
        finally:
            _cleanup_passlog(passlog)
        results.append(result)
    return results


def execute_preview(job: PreviewJob, ffmpeg_path: Path, workdir: Path) -> PreviewResult:
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, job.source_path, "preview")
    extract_cmd = build_preview_extract_command(ffmpeg_path, job)
    encode_cmds, passlog = build_preview_encode_commands(ffmpeg_path, job, workdir)

    try:
        _run_logged_command(extract_cmd, log_path)
        for cmd in encode_cmds:
            _run_logged_command(cmd, log_path)
        result = estimate_preview(job)
        result.log_path = log_path
        return result
    except subprocess.CalledProcessError as exc:
        return PreviewResult(
            job=job,
            success=False,
            notes=list(job.notes),
            log_path=log_path,
            error_message=exc.stderr or exc.stdout or str(exc),
        )
    finally:
        _cleanup_passlog(passlog)
