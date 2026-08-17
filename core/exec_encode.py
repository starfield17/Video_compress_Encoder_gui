from __future__ import annotations

import os
import re
import subprocess
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, TextIO

from core.analysis_concurrency import analysis_concurrency_limit
from core.build_ffmpeg_cmd import (
    build_encode_commands,
    build_preview_encode_commands,
    build_preview_extract_command,
)
from core.constraint_resolution import reselect_after_quality_decision
from core.external_subtitles import copy_external_subtitles
from core.models import (
    CompressionMode,
    ConstraintFailureKind,
    ConstraintPolicy,
    DecisionActionCode,
    EncodePlan,
    EncodePlanItem,
    EncodeResult,
    OperationCancelledError,
    PreviewJob,
    PreviewResult,
    QualitySearchResult,
    QualitySearchStatus,
    QualityUnreachablePolicy,
    SmartPreviewResult,
)
from core.path_utils import log_file_path
from core.preview_estimate import estimate_preview
from core.safety_checks import validate_workdir
from core.subprocess_utils import hidden_popen_kwargs
from core.smart_quality import (
    SMART_ANALYSIS_SEMAPHORE,
    acquire_analysis_slot,
    analyze_quality,
    build_decision_options,
    constraint_policy_from_size_blocked,
    resolve_max_output_ratio,
)


def _apply_constraint_policy(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    quality_result: QualitySearchResult,
    policy: ConstraintPolicy,
) -> QualitySearchResult:
    action_code = {
        ConstraintPolicy.RELAX_SIZE: DecisionActionCode.RELAX_SIZE,
        ConstraintPolicy.RELAX_QUALITY: DecisionActionCode.RELAX_QUALITY,
    }.get(policy)
    if action_code is None:
        return quality_result
    decision = next(
        (option for option in build_decision_options(quality_result) if option.action_code == action_code),
        None,
    )
    if decision is None or decision.requires_analysis:
        return quality_result
    return reselect_after_quality_decision(
        ffmpeg_path,
        item,
        quality_result,
        decision,
    )


def _size_miss_output_path(output_path: Path) -> Path:
    return output_path.with_name(
        f"{output_path.stem}.size-miss-{uuid.uuid4().hex[:8]}{output_path.suffix}"
    )


def _usable_smart_result(item: EncodePlanItem) -> QualitySearchResult | None:
    result = item.quality_search_result
    if result is None or not result.success:
        return None
    encoder = item.encoder_info
    if encoder is None:
        return None
    if result.encoder_name != encoder.encoder_name or result.backend != encoder.backend:
        return None
    return result


def item_needs_smart_analysis(item: EncodePlanItem) -> bool:
    return (
        item.skip_reason is None
        and item.options.compression_mode == CompressionMode.SMART
        and _usable_smart_result(item) is None
    )


def _assert_quality_encoder_matches_item(
    item: EncodePlanItem,
    quality_result: QualitySearchResult,
) -> None:
    encoder = item.encoder_info
    if encoder is None:
        raise ValueError("Smart encoding requires a bound encoder.")
    if quality_result.encoder_name != encoder.encoder_name or quality_result.backend != encoder.backend:
        raise RuntimeError(
            "Smart analysis result was produced by a different encoder "
            f"({quality_result.encoder_name}/{quality_result.backend.value}); expected "
            f"{encoder.encoder_name}/{encoder.backend.value}."
        )


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
    # Try graceful termination first, then force-kill if the process stays alive.
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


def _cancel_requested(cancel_check: Callable[[], bool] | None) -> bool:
    return cancel_check is not None and cancel_check()


def _stop_running_command(
    proc: subprocess.Popen[str],
    log_callback: Callable[[str], None] | None,
    progress_callback: Callable[[dict[str, object]], None] | None,
    progress_context: dict[str, object] | None,
) -> None:
    message = "Cancellation requested. Stopping ffmpeg..."
    _emit(log_callback, message)
    _emit_progress(
        progress_callback,
        category="status",
        state="cancelling",
        message=message,
        **(progress_context or {}),
    )
    _cancel_process(proc)
    raise OperationCancelledError("Operation cancelled.")


def _emit_command_line(
    log_file: TextIO,
    cmd: list[str],
    log_callback: Callable[[str], None] | None,
    progress_callback: Callable[[dict[str, object]], None] | None,
    progress_context: dict[str, object] | None,
) -> None:
    command_line = "$ " + " ".join(cmd)
    log_file.write(command_line + "\n")
    log_file.flush()
    _emit(log_callback, command_line)
    _emit_progress(
        progress_callback,
        category="command",
        message=command_line,
        **(progress_context or {}),
    )


def _start_command_process(cmd: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **hidden_popen_kwargs(),
    )


def _apply_pass_progress(
    event: dict[str, object],
    progress_context: dict[str, object] | None,
) -> dict[str, object]:
    if not progress_context:
        return event

    current_pass_index = progress_context.get("current_pass_index")
    total_passes = progress_context.get("total_passes")
    if not isinstance(current_pass_index, int) or not isinstance(total_passes, int) or total_passes <= 0:
        return event

    # Remap intra-pass percentage to total file percentage across N passes.
    raw_percent = event.get("percent")
    pass_percent = float(raw_percent) if isinstance(raw_percent, (int, float)) else 0.0
    file_progress = (((current_pass_index - 1) + (pass_percent / 100.0)) / total_passes) * 100.0
    event["pass_percent"] = pass_percent
    event["file_progress"] = max(0.0, min(100.0, file_progress))
    event["percent"] = event["file_progress"]
    return event


def _emit_output_event(
    normalized: str,
    progress_callback: Callable[[dict[str, object]], None] | None,
    progress_context: dict[str, object] | None,
) -> None:
    raw_duration = progress_context.get("duration_sec") if progress_context else None
    duration_sec = float(raw_duration) if isinstance(raw_duration, (int, float)) else None
    parsed = _parse_ffmpeg_progress(normalized, duration_sec)
    if parsed is None:
        _emit_progress(progress_callback, category="log", message=normalized, **(progress_context or {}))
        return

    event = _apply_pass_progress(dict(parsed), progress_context)
    _emit_progress(
        progress_callback,
        category="ffmpeg",
        message=normalized,
        **(progress_context or {}),
        **event,
    )


def _handle_output_line(
    line: str,
    log_file: TextIO,
    output_chunks: list[str],
    log_callback: Callable[[str], None] | None,
    progress_callback: Callable[[dict[str, object]], None] | None,
    progress_context: dict[str, object] | None,
) -> None:
    normalized = line.rstrip("\r\n")
    output_chunks.append(line)
    log_file.write(line)
    log_file.flush()
    if not normalized:
        return
    _emit(log_callback, normalized)
    _emit_output_event(normalized, progress_callback, progress_context)


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
        _emit_command_line(log_file, cmd, log_callback, progress_callback, progress_context)
        proc = _start_command_process(cmd)
        if process_callback is not None:
            process_callback(proc)
        output_chunks: list[str] = []
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if _cancel_requested(cancel_check):
                    _stop_running_command(proc, log_callback, progress_callback, progress_context)
                _handle_output_line(line, log_file, output_chunks, log_callback, progress_callback, progress_context)
            return_code = proc.wait()
            log_file.write("\n")
            log_file.flush()
        finally:
            if process_callback is not None:
                process_callback(None)

    stdout_text = "".join(output_chunks)
    # Catch a cancellation that arrived after the last stdout line but before
    # the process was waited on.
    if _cancel_requested(cancel_check):
        raise OperationCancelledError("Operation cancelled.")
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd, output=stdout_text)
    return subprocess.CompletedProcess(
        cmd,
        return_code,
        stdout=stdout_text,
        stderr="",
    )


def _encode_progress_context(
    item: EncodePlanItem,
    queue_index: int,
    queue_total: int,
    extra_progress_context: dict[str, object] | None,
) -> dict[str, object]:
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
    return base_context


def _skipped_encode_result(
    item: EncodePlanItem,
    log_path: Path,
    base_context: dict[str, object],
    queue_index: int,
    queue_total: int,
    log_callback: Callable[[str], None] | None,
    progress_callback: Callable[[dict[str, object]], None] | None,
) -> EncodeResult:
    # Items that failed during planning are surfaced as skipped results so the
    # rest of the batch can continue.
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


def _copy_external_subtitles_for_result(
    item: EncodePlanItem,
    result: EncodeResult,
    queue_index: int,
    queue_total: int,
    log_callback: Callable[[str], None] | None,
) -> None:
    if not item.options.copy_external_subtitles:
        return

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


def _write_command_failure_log(log_path: Path, exc: subprocess.CalledProcessError) -> None:
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"[command failed] returncode={exc.returncode}\n")
        if exc.stdout:
            fh.write(exc.stdout + "\n")
        if exc.stderr:
            fh.write(exc.stderr + "\n")


def analyze_plan_item(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    workdir: Path,
    *,
    queue_index: int = 1,
    queue_total: int = 1,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
    extra_progress_context: dict[str, object] | None = None,
    constraint_policy: ConstraintPolicy | None = None,
) -> EncodeResult | None:
    """Run Smart analysis and attach the result.

    Returns an ``EncodeResult`` when the item must not be encoded (skip,
    unsupported, or needs a decision). Returns ``None`` when the item is ready
    to encode with ``item.target_video_bitrate_bps`` set.
    """
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, item.source_path, "encode")
    base_context = _encode_progress_context(item, queue_index, queue_total, extra_progress_context)
    if item.skip_reason:
        return _skipped_encode_result(
            item,
            log_path,
            base_context,
            queue_index,
            queue_total,
            log_callback,
            progress_callback,
        )
    if item.options.compression_mode != CompressionMode.SMART:
        return None

    existing = _usable_smart_result(item)
    if existing is not None:
        item.target_video_bitrate_bps = existing.selected_video_bitrate_bps
        return None

    result = EncodeResult(
        source_path=item.source_path,
        output_path=item.output_path,
        success=True,
        log_path=log_path,
    )
    _emit(log_callback, f"[{queue_index}/{queue_total}] Waiting for smart analysis: {item.source_path.name}")
    _emit_progress(
        progress_callback,
        state="waiting_analysis",
        percent=0.0,
        file_progress=0.0,
        **base_context,
    )
    acquire_analysis_slot(cancel_check)
    try:
        _emit(log_callback, f"[{queue_index}/{queue_total}] Smart analysis started: {item.source_path.name}")

        def analysis_progress(event: dict[str, object]) -> None:
            _emit_progress(progress_callback, **{**base_context, **event})

        quality_result = analyze_quality(
            ffmpeg_path,
            item,
            workdir,
            log_path,
            progress_callback=analysis_progress,
            cancel_check=cancel_check,
            process_callback=process_callback,
        )
    finally:
        SMART_ANALYSIS_SEMAPHORE.release()

    item.quality_search_result = quality_result
    result.quality_search_result = quality_result
    _assert_quality_encoder_matches_item(item, quality_result)
    if quality_result.status == QualitySearchStatus.CONSTRAINT_UNSATISFIED:
        size_policy = constraint_policy
        if size_policy is None:
            size_policy = constraint_policy_from_size_blocked(item.options.size_blocked_policy)
        if quality_result.failure_kind == ConstraintFailureKind.SIZE_BLOCKED:
            applied = _apply_constraint_policy(ffmpeg_path, item, quality_result, size_policy)
            if applied.success and size_policy != ConstraintPolicy.FAIL:
                _emit(
                    log_callback,
                    f"[{queue_index}/{queue_total}] Applied {size_policy.value} for "
                    f"{item.source_path.name}: bitrate={applied.selected_video_bitrate_bps} "
                    f"VMAF={applied.min_vmaf}",
                )
            quality_result = applied
        item.quality_search_result = quality_result
        result.quality_search_result = quality_result
    if not quality_result.success:
        result.success = False
        result.error_message = quality_result.reason or "Smart compression constraints could not be satisfied."
        unreachable_skip = (
            quality_result.status == QualitySearchStatus.CONSTRAINT_UNSATISFIED
            and quality_result.failure_kind == ConstraintFailureKind.QUALITY_UNREACHABLE
            and item.options.quality_unreachable_policy == QualityUnreachablePolicy.SKIP
        )
        result.needs_decision = (
            quality_result.status == QualitySearchStatus.CONSTRAINT_UNSATISFIED and not unreachable_skip
        )
        result.skipped = not result.needs_decision
        _emit(
            log_callback,
            f"[{queue_index}/{queue_total}] Smart analysis requires a decision for "
            f"{item.source_path.name}: {result.error_message}"
            if result.needs_decision
            else f"[{queue_index}/{queue_total}] Smart analysis skipped "
            f"{item.source_path.name}: {result.error_message}",
        )
        _emit_progress(
            progress_callback,
            state="needs_decision" if result.needs_decision else "skipped",
            percent=100.0,
            file_progress=100.0,
            message=result.error_message,
            quality_search_result=quality_result,
            **base_context,
        )
        return result

    item.target_video_bitrate_bps = quality_result.selected_video_bitrate_bps
    _emit_progress(
        progress_callback,
        state="analysis_finished",
        quality_search_result=quality_result,
        target_video_bitrate_bps=item.target_video_bitrate_bps,
        **base_context,
    )
    return None


def run_analysis_phase(
    ffmpeg_path: Path,
    items: list[EncodePlanItem],
    workdir: Path,
    *,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[str, subprocess.Popen[str] | None], None] | None = None,
    item_contexts: list[dict[str, object]] | None = None,
    pause_check: Callable[[], bool] | None = None,
    item_started_callback: Callable[[int], None] | None = None,
    item_result_callback: Callable[[int, EncodeResult], None] | None = None,
    constraint_policy: ConstraintPolicy | None = None,
) -> list[EncodeResult | None]:
    """Analyze every Smart item before any encode starts.

    Items that are ready to encode keep ``None`` in the returned list. Terminal
    analysis outcomes (skip / decision / unsupported) are stored in place.
    """
    results: list[EncodeResult | None] = [None] * len(items)
    pending = deque(
        index for index, item in enumerate(items) if item_needs_smart_analysis(item) or item.skip_reason
    )
    if not pending:
        return results

    workers = max(1, min(analysis_concurrency_limit(), len(pending)))
    lock = threading.Lock()
    stop_event = threading.Event()
    exceptions: list[BaseException] = []

    def should_stop() -> bool:
        return stop_event.is_set() or (cancel_check is not None and cancel_check())

    def worker(slot: str) -> None:
        while not should_stop():
            if pause_check is not None and pause_check():
                return
            with lock:
                if not pending:
                    return
                index = pending.popleft()
            item = items[index]
            context = dict(item_contexts[index]) if item_contexts and index < len(item_contexts) else {}
            if item_started_callback is not None:
                item_started_callback(index)

            def slot_process(proc: subprocess.Popen[str] | None, worker_slot: str = slot) -> None:
                if process_callback is not None:
                    process_callback(worker_slot, proc)

            try:
                terminal = analyze_plan_item(
                    ffmpeg_path,
                    item,
                    workdir,
                    queue_index=index + 1,
                    queue_total=len(items),
                    log_callback=log_callback,
                    progress_callback=progress_callback,
                    cancel_check=should_stop,
                    process_callback=slot_process if process_callback is not None else None,
                    extra_progress_context=context,
                    constraint_policy=constraint_policy,
                )
            except BaseException as exc:
                with lock:
                    exceptions.append(exc)
                stop_event.set()
                return
            if terminal is not None:
                results[index] = terminal
                if item_result_callback is not None:
                    item_result_callback(index, terminal)

    _emit(log_callback, f"Smart analysis phase started ({len(pending)} file(s), concurrency={workers}).")
    _emit_progress(
        progress_callback,
        stage="analysis",
        state="started",
        parallel=workers > 1,
        percent=0.0,
    )
    threads = [
        threading.Thread(target=worker, args=(f"analysis-{slot}",), daemon=True)
        for slot in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if cancel_check is not None and cancel_check():
        raise OperationCancelledError("Smart analysis cancelled.")
    if exceptions:
        raise exceptions[0]
    _emit(log_callback, "Smart analysis phase finished.")
    _emit_progress(
        progress_callback,
        stage="analysis",
        state="finished",
        parallel=workers > 1,
        percent=100.0,
    )
    return results


def execute_plan_item(
    ffmpeg_path: Path,
    item: EncodePlanItem,
    workdir: Path,
    *,
    queue_index: int = 1,
    queue_total: int = 1,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
    extra_progress_context: dict[str, object] | None = None,
    constraint_policy: ConstraintPolicy | None = None,
) -> EncodeResult:
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, item.source_path, "encode")
    base_context = _encode_progress_context(item, queue_index, queue_total, extra_progress_context)

    if item.skip_reason:
        return _skipped_encode_result(
            item,
            log_path,
            base_context,
            queue_index,
            queue_total,
            log_callback,
            progress_callback,
        )

    result = EncodeResult(
        source_path=item.source_path,
        output_path=item.output_path,
        success=True,
        log_path=log_path,
    )
    passlog: Path | None = None
    temporary_output: Path | None = None
    commands: list[list[str]] = []
    total_passes = 1
    current_pass_index = 1
    try:
        if item.options.compression_mode == CompressionMode.SMART:
            terminal = analyze_plan_item(
                ffmpeg_path,
                item,
                workdir,
                queue_index=queue_index,
                queue_total=queue_total,
                log_callback=log_callback,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                process_callback=process_callback,
                extra_progress_context=extra_progress_context,
                constraint_policy=constraint_policy,
            )
            if terminal is not None:
                return terminal
            result.quality_search_result = item.quality_search_result
            item.output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_output = item.output_path.parent / (
                f".{item.output_path.stem}.smart-{uuid.uuid4().hex}{item.output_path.suffix}"
            )

        commands, passlog = build_encode_commands(
            ffmpeg_path,
            item,
            workdir,
            output_path=temporary_output,
        )
        result.commands = commands
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
        if temporary_output is not None:
            if result.quality_search_result is not None:
                _assert_quality_encoder_matches_item(item, result.quality_search_result)
            _emit_progress(
                progress_callback,
                state="validating",
                percent=100.0,
                file_progress=100.0,
                current_pass_index=total_passes,
                total_passes=total_passes,
                **base_context,
            )
            max_output_bytes = int(
                item.source_path.stat().st_size
                * resolve_max_output_ratio(item.options.codec, item.options.max_output_ratio)
            )
            actual_size = temporary_output.stat().st_size
            if actual_size > max_output_bytes:
                result.success = False
                result.needs_decision = True
                result.actual_output_bytes = actual_size
                result.allowed_output_bytes = max_output_bytes
                rejected_output = _size_miss_output_path(item.output_path)
                os.replace(temporary_output, rejected_output)
                temporary_output = None
                result.rejected_output_path = rejected_output
                result.error_message = (
                    f"Actual output size {actual_size} bytes exceeds the smart limit "
                    f"of {max_output_bytes} bytes. The encoded file was preserved at {rejected_output}."
                )
                _emit(log_callback, f"[{queue_index}/{queue_total}] {result.error_message}")
                _emit_progress(
                    progress_callback,
                    state="needs_decision",
                    message=result.error_message,
                    quality_search_result=result.quality_search_result,
                    **base_context,
                )
                return result
            if item.output_path.exists() and not item.options.overwrite:
                raise FileExistsError(f"Output appeared during encoding and overwrite is disabled: {item.output_path}")
            os.replace(temporary_output, item.output_path)
            temporary_output = None
        _copy_external_subtitles_for_result(item, result, queue_index, queue_total, log_callback)
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
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as exc:
        result.success = False
        if isinstance(exc, subprocess.CalledProcessError):
            result.return_code = exc.returncode
            result.error_message = exc.stderr or exc.stdout or str(exc)
            _write_command_failure_log(log_path, exc)
        else:
            result.return_code = 1
            result.error_message = str(exc)
        failure_activity = (
            f"[{queue_index}/{queue_total}] Failed {item.source_path.name} "
            f"(exit code {result.return_code}); see log: {log_path}"
        )
        _emit(log_callback, failure_activity)
        _emit_progress(
            progress_callback,
            state="failed_file",
            message=failure_activity,
            current_pass_index=total_passes,
            total_passes=total_passes,
            **base_context,
        )
        return result
    finally:
        _cleanup_passlog(passlog)
        if temporary_output is not None:
            try:
                temporary_output.unlink(missing_ok=True)
            except OSError:
                pass


def _cleanup_passlog(passlog: Path | None) -> None:
    # ffmpeg multi-pass writes pass log files that are useless after encoding;
    # glob removes the log and any numbered variants it created.
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
    constraint_policy: ConstraintPolicy | None = None,
    pause_check: Callable[[], bool] | None = None,
    item_started_callback: Callable[[int], None] | None = None,
    item_result_callback: Callable[[int, EncodeResult], None] | None = None,
    extra_progress_contexts: list[dict[str, object]] | None = None,
) -> list[EncodeResult]:
    workdir = validate_workdir(workdir)
    total = len(plan.items)

    def analysis_process(slot: str, proc: subprocess.Popen[str] | None) -> None:
        del slot
        if process_callback is not None:
            process_callback(proc)

    results = run_analysis_phase(
        plan.ffmpeg_path,
        plan.items,
        workdir,
        log_callback=log_callback,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
        process_callback=analysis_process if process_callback is not None else None,
        item_contexts=extra_progress_contexts,
        pause_check=pause_check,
        item_started_callback=item_started_callback,
        item_result_callback=item_result_callback,
        constraint_policy=constraint_policy,
    )
    if pause_check is not None and pause_check():
        return [result for result in results if result is not None]

    _emit(log_callback, "Encode phase started.")
    _emit_progress(progress_callback, stage="encode", state="started", percent=0.0)
    for index, item in enumerate(plan.items):
        if results[index] is not None:
            continue
        if cancel_check is not None and cancel_check():
            _emit(log_callback, "Encode execution cancelled by user.")
            _emit_progress(progress_callback, stage="encode", state="cancelled")
            raise OperationCancelledError("Encoding cancelled.")
        if pause_check is not None and pause_check():
            break
        if item_started_callback is not None and item.options.compression_mode != CompressionMode.SMART:
            item_started_callback(index)
        context = extra_progress_contexts[index] if extra_progress_contexts and index < len(extra_progress_contexts) else None
        encoded = execute_plan_item(
            plan.ffmpeg_path,
            item,
            workdir,
            queue_index=index + 1,
            queue_total=total,
            log_callback=log_callback,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            process_callback=process_callback,
            extra_progress_context=context,
            constraint_policy=constraint_policy,
        )
        results[index] = encoded
        if item_result_callback is not None:
            item_result_callback(index, encoded)
    completed = [result for result in results if result is not None]
    paused = pause_check is not None and pause_check() and len(completed) < total
    _emit(log_callback, "Encode execution paused." if paused else "Encode execution finished.")
    _emit_progress(
        progress_callback,
        stage="encode",
        state="paused" if paused else "finished",
        percent=100.0 if not paused else None,
    )
    return completed


def execute_preview(
    job: PreviewJob,
    ffmpeg_path: Path,
    workdir: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
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


def execute_smart_preview(
    item: EncodePlanItem,
    ffmpeg_path: Path,
    workdir: Path,
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> SmartPreviewResult:
    workdir = validate_workdir(workdir)
    log_path = log_file_path(workdir, item.source_path, "smart-preview")
    try:
        acquire_analysis_slot(cancel_check)
        try:
            quality_result = analyze_quality(
                ffmpeg_path,
                item,
                workdir,
                log_path,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                process_callback=process_callback,
            )
        finally:
            SMART_ANALYSIS_SEMAPHORE.release()
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
