from __future__ import annotations

import copy
import threading
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

from core.encoder_caps import list_available_encoders, resolve_encoder
from core.exec_encode import execute_plan_item
from core.models import BackendChoice, EncodePlan, EncodePlanItem, EncodeResult, OperationCancelledError
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
) -> list[EncodeResult]:
    workdir = validate_workdir(workdir)
    normalized = validate_parallel_options(backends, plan)
    available_encoders = list_available_encoders(plan.ffmpeg_path)
    encoders = {
        backend: resolve_encoder(plan.items[0].options.codec, backend, available_encoders)
        for backend in normalized
    }
    pending = deque(enumerate(plan.items))
    lock = threading.Lock()
    stop_event = threading.Event()
    results: list[EncodeResult | None] = [None] * len(plan.items)
    exceptions: list[BaseException] = []
    total = len(plan.items)

    def should_stop() -> bool:
        return stop_event.is_set() or (cancel_check is not None and cancel_check())

    def worker(backend: BackendChoice) -> None:
        encoder = encoders[backend]
        worker_name = backend.value
        while not should_stop():
            if pause_check is not None and pause_check():
                return
            with lock:
                if not pending:
                    return
                index, item = pending.popleft()
            try:
                bound_item = _bind_item_to_backend(item, backend, encoder)
                context = _context_for_item(item_contexts, index, backend, encoder.encoder_name)
                if item_started_callback is not None:
                    item_started_callback(index, backend.value, encoder.encoder_name)
                callback = None
                if process_callback is not None:
                    callback = lambda proc, name=worker_name: process_callback(name, proc)
                result = execute_plan_item(
                    plan.ffmpeg_path,
                    bound_item,
                    workdir,
                    queue_index=index + 1,
                    queue_total=total,
                    log_callback=log_callback,
                    progress_callback=progress_callback,
                    cancel_check=should_stop,
                    process_callback=callback,
                    extra_progress_context=context,
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
        log_callback(f"Parallel encode execution started with {len(normalized)} backend worker(s).")
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
