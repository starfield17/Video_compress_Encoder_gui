from __future__ import annotations

import copy
import threading
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

from core.app_paths import config_dir as app_config_dir
from core.encoder_capability_cache import ensure_encoder_capabilities
from core.encoder_caps import resolve_encoder
from core.exec_encode import execute_plan_item, run_analysis_phase
from core.models import (
    BackendChoice,
    CompressionMode,
    ConstraintPolicy,
    EncodePlan,
    EncodePlanItem,
    EncodeResult,
    OperationCancelledError,
)
from core.safety_checks import validate_workdir


ProgressContext = dict[str, object]
ProcessCallback = Callable[[str, object | None], None]
ItemStartedCallback = Callable[[int, str, str], None]
ItemResultCallback = Callable[[int, EncodeResult], None]


def normalize_parallel_backends(backends: Sequence[BackendChoice]) -> tuple[BackendChoice, ...]:
    normalized: list[BackendChoice] = []
    seen: set[BackendChoice] = set()
    for backend in backends:
        backend_choice = BackendChoice(backend)
        if backend_choice == BackendChoice.AUTO:
            continue
        if backend_choice in seen:
            continue
        seen.add(backend_choice)
        normalized.append(backend_choice)
    return tuple(normalized)


def validate_parallel_options(backends: Sequence[BackendChoice], plan: EncodePlan | None = None) -> tuple[BackendChoice, ...]:
    normalized = normalize_parallel_backends(backends)
    if not normalized:
        raise ValueError("Parallel mode requires at least one explicit backend.")
    if plan is None:
        return normalized
    for item in plan.items:
        if item.options.two_pass:
            raise ValueError("Parallel mode does not support two-pass encoding.")
        if item.options.encoder_preset:
            raise ValueError("Parallel mode does not support a manually entered encoder preset.")
    return normalized


def _bind_item_to_backend(
    item: EncodePlanItem,
    backend: BackendChoice,
    encoder_info,
) -> EncodePlanItem:
    cloned = copy.deepcopy(item)
    cloned.encoder_info = encoder_info
    cloned.options = replace(
        cloned.options,
        backend=backend,
        two_pass=False,
        encoder_preset=encoder_info.default_preset,
    )
    return cloned


def _context_for_item(
    contexts: Sequence[ProgressContext] | None,
    index: int,
    backend: BackendChoice,
    encoder_name: str,
) -> ProgressContext:
    context = dict(contexts[index]) if contexts and index < len(contexts) else {}
    context["queue_backend"] = backend.value
    context["queue_encoder"] = encoder_name
    return context


def _first_exception(exceptions: list[BaseException]) -> BaseException | None:
    return exceptions[0] if exceptions else None


def _process_callback_for_worker(
    process_callback: ProcessCallback | None,
    worker_name: str,
) -> Callable[[object | None], None] | None:
    if process_callback is None:
        return None

    def callback(proc: object | None) -> None:
        process_callback(worker_name, proc)

    return callback


def execute_plan_parallel(
    plan: EncodePlan,
    workdir: Path,
    *,
    backends: tuple[BackendChoice, ...],
    log_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    process_callback: ProcessCallback | None = None,
    item_contexts: Sequence[ProgressContext] | None = None,
    pause_check: Callable[[], bool] | None = None,
    item_started_callback: ItemStartedCallback | None = None,
    item_result_callback: ItemResultCallback | None = None,
    constraint_policy: ConstraintPolicy | None = None,
) -> list[EncodeResult]:
    # One daemon thread per backend pulls work from a lock-protected deque.
    workdir = validate_workdir(workdir)
    normalized = validate_parallel_options(backends, plan)
    runtime_capabilities = ensure_encoder_capabilities(app_config_dir(), plan.ffmpeg_path)
    encoders = {
        backend: resolve_encoder(
            plan.items[0].options.codec,
            backend,
            set(),
            plan.ffmpeg_path,
            runtime_capabilities=runtime_capabilities,
        )
        for backend in normalized
    }
    bound_items: list[EncodePlanItem] = []
    for index, item in enumerate(plan.items):
        backend = normalized[index % len(normalized)]
        bound_items.append(_bind_item_to_backend(item, backend, encoders[backend]))

    analysis_contexts = []
    for index, bound in enumerate(bound_items):
        encoder = bound.encoder_info
        analysis_contexts.append(
            _context_for_item(
                item_contexts,
                index,
                bound.options.backend,
                encoder.encoder_name if encoder is not None else "",
            )
        )

    def analysis_started(index: int) -> None:
        bound = bound_items[index]
        encoder = bound.encoder_info
        if item_started_callback is not None and encoder is not None:
            item_started_callback(index, encoder.backend.value, encoder.encoder_name)

    if log_callback is not None:
        log_callback(f"Parallel execution started with {len(normalized)} backend(s); analysis runs first.")
    results = run_analysis_phase(
        plan.ffmpeg_path,
        bound_items,
        workdir,
        log_callback=log_callback,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
        process_callback=process_callback,
        item_contexts=analysis_contexts,
        pause_check=pause_check,
        item_started_callback=analysis_started,
        item_result_callback=item_result_callback,
        constraint_policy=constraint_policy,
    )
    if pause_check is not None and pause_check():
        return [result for result in results if result is not None]

    pending = deque(
        (index, item)
        for index, item in enumerate(bound_items)
        if results[index] is None
    )
    lock = threading.Lock()
    stop_event = threading.Event()
    exceptions: list[BaseException] = []
    total = len(plan.items)

    def should_stop() -> bool:
        return stop_event.is_set() or (cancel_check is not None and cancel_check())

    def worker(backend: BackendChoice) -> None:
        worker_name = backend.value
        while not should_stop():
            if pause_check is not None and pause_check():
                return
            with lock:
                claimed: tuple[int, EncodePlanItem] | None = None
                for offset, (index, item) in enumerate(pending):
                    if item.options.backend == backend:
                        claimed = pending[offset]
                        del pending[offset]
                        break
                if claimed is None:
                    return
                index, item = claimed
            try:
                context = _context_for_item(item_contexts, index, backend, encoders[backend].encoder_name)
                if item_started_callback is not None and item.options.compression_mode != CompressionMode.SMART:
                    item_started_callback(index, backend.value, encoders[backend].encoder_name)
                callback = _process_callback_for_worker(process_callback, worker_name)
                result = execute_plan_item(
                    plan.ffmpeg_path,
                    item,
                    workdir,
                    queue_index=index + 1,
                    queue_total=total,
                    log_callback=log_callback,
                    progress_callback=progress_callback,
                    cancel_check=should_stop,
                    process_callback=callback,
                    extra_progress_context=context,
                    constraint_policy=constraint_policy,
                )
                results[index] = result
                if item_result_callback is not None:
                    item_result_callback(index, result)
            except BaseException as exc:
                with lock:
                    exceptions.append(exc)
                stop_event.set()
                return

    if log_callback is not None:
        log_callback(f"Encode phase started with {len(normalized)} backend worker(s).")
    if progress_callback is not None:
        progress_callback({"stage": "encode", "state": "started", "parallel": True, "percent": 0.0})

    threads = [threading.Thread(target=worker, args=(backend,), daemon=True) for backend in normalized]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if cancel_check is not None and cancel_check():
        raise OperationCancelledError("Encoding cancelled.")
    first_error = _first_exception(exceptions)
    if first_error is not None:
        raise first_error
    ordered_results = [result for result in results if result is not None]
    paused = pause_check is not None and pause_check() and len(ordered_results) < total
    if progress_callback is not None:
        progress_callback(
            {
                "stage": "encode",
                "state": "paused" if paused else "finished",
                "parallel": True,
                "percent": 100.0 if not paused else None,
            }
        )
    if log_callback is not None:
        log_callback("Parallel encode execution paused." if paused else "Parallel encode execution finished.")
    return ordered_results
