from __future__ import annotations

import copy
import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from core.exec_encode import execute_plan_item
from core.models import EncodePlan, EncodeResult, OperationCancelledError
from core.parallel_queue_exec import execute_plan_parallel, normalize_parallel_backends
from gui.queue_state import QueueItemRecord, create_queue_records
from gui.queue_table import QueueTableModel


@dataclass(slots=True)
class QueueExecutionItem:
    item_id: str
    record: QueueItemRecord


class QueueExecuteWorker(QThread):
    log = Signal(str)
    progress = Signal(object)
    item_started = Signal(str, str, str)
    item_finished = Signal(str, object)
    paused = Signal()
    cancelled = Signal(str)
    failed = Signal(str)
    queue_finished = Signal()

    def __init__(self, items: list[QueueExecutionItem], parent=None) -> None:
        super().__init__(parent)
        self.items = items
        self._cancel_event = threading.Event()
        self._pause_after_current_event = threading.Event()
        self._current_processes: dict[str, object] = {}

    def _emit_log(self, message: str) -> None:
        self.log.emit(message)

    def _emit_progress(self, event: dict[str, object]) -> None:
        self.progress.emit(event)

    def _set_current_process(self, slot: str, proc) -> None:
        if proc is None:
            self._current_processes.pop(slot, None)
            return
        self._current_processes[slot] = proc

    def cancel(self) -> None:
        self._cancel_event.set()
        for proc in list(self._current_processes.values()):
            try:
                proc.terminate()
            except Exception:
                pass

    def pause_after_current(self) -> None:
        self._pause_after_current_event.set()

    def _parallel_config(self) -> tuple[bool, tuple]:
        if not self.items:
            return False, ()
        first = self.items[0].record.plan_item.options
        enabled = first.parallel_enabled
        backends = normalize_parallel_backends(first.parallel_backends)
        for item in self.items[1:]:
            options = item.record.plan_item.options
            if options.parallel_enabled != enabled:
                raise ValueError("Queued items use mixed parallel settings.")
            if normalize_parallel_backends(options.parallel_backends) != backends:
                raise ValueError("Queued items use different parallel backend selections.")
        return enabled, backends

    def _build_plan(self) -> EncodePlan:
        first_record = self.items[0].record
        return EncodePlan(
            items=[copy.deepcopy(item.record.plan_item) for item in self.items],
            ffmpeg_path=first_record.job_snapshot.ffmpeg_path,
            ffprobe_path=first_record.job_snapshot.ffprobe_path,
            input_root=first_record.source_path.parent,
            output_root=first_record.job_snapshot.output_root,
        )

    def run(self) -> None:
        try:
            parallel_enabled, parallel_backends = self._parallel_config()
            if parallel_enabled and parallel_backends:
                plan = self._build_plan()
                index_to_item_id = [item.item_id for item in self.items]
                results = execute_plan_parallel(
                    plan,
                    self.items[0].record.job_snapshot.workdir,
                    backends=parallel_backends,
                    log_callback=self._emit_log,
                    progress_callback=self._emit_progress,
                    cancel_check=self._cancel_event.is_set,
                    pause_check=self._pause_after_current_event.is_set,
                    process_callback=self._set_current_process,
                    item_contexts=[{"queue_item_id": item.item_id} for item in self.items],
                    item_started_callback=lambda index, backend, encoder: self.item_started.emit(
                        index_to_item_id[index], backend, encoder
                    ),
                    item_result_callback=lambda index, result: self.item_finished.emit(index_to_item_id[index], result),
                )
                if self._pause_after_current_event.is_set() and len(results) < len(self.items):
                    self.paused.emit()
                    return
                self.queue_finished.emit()
                return
            for index, item in enumerate(self.items, start=1):
                if self._cancel_event.is_set():
                    raise OperationCancelledError("Encoding cancelled.")
                encoder = item.record.plan_item.encoder_info
                backend_name = encoder.backend.value if encoder else item.record.plan_item.options.backend.value
                encoder_name = encoder.encoder_name if encoder else "n/a"
                self.item_started.emit(item.item_id, backend_name, encoder_name)
                result = execute_plan_item(
                    item.record.job_snapshot.ffmpeg_path,
                    copy.deepcopy(item.record.plan_item),
                    item.record.job_snapshot.workdir,
                    queue_index=index,
                    queue_total=len(self.items),
                    log_callback=self._emit_log,
                    progress_callback=self._emit_progress,
                    cancel_check=self._cancel_event.is_set,
                    process_callback=lambda proc: self._set_current_process("serial", proc),
                    extra_progress_context={"queue_item_id": item.item_id},
                )
                self.item_finished.emit(item.item_id, result)
                if self._pause_after_current_event.is_set():
                    self.paused.emit()
                    return
            self.queue_finished.emit()
        except OperationCancelledError as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class QueueManager(QObject):
    log = Signal(str)
    progress = Signal(object)
    busyChanged = Signal(bool)
    stateChanged = Signal(str)
    error = Signal(str)

    def __init__(self, model: QueueTableModel, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self._worker: QueueExecuteWorker | None = None
        self._active_item_ids: set[str] = set()
        self._pause_after_current_requested = False

    def is_busy(self) -> bool:
        return self._worker is not None

    def add_plan(self, plan, workdir) -> int:
        records = create_queue_records(plan, workdir)
        self.model.add_records(records)
        return len(records)

    def start(self) -> bool:
        if self._worker is not None:
            return False
        execution_records = self.model.execution_records()
        if not execution_records:
            return False

        items = [QueueExecutionItem(item_id=record.item_id, record=record) for record in execution_records]
        self.model.prepare_for_execution([item.item_id for item in items])
        self._pause_after_current_requested = False
        self._worker = QueueExecuteWorker(items)
        self._worker.log.connect(self.log.emit)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.item_started.connect(self._on_item_started)
        self._worker.item_finished.connect(self._on_item_finished)
        self._worker.paused.connect(self._on_worker_paused)
        self._worker.cancelled.connect(self._on_worker_cancelled)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.queue_finished.connect(self._on_worker_queue_finished)
        self._worker.finished.connect(self._on_worker_thread_finished)
        self.busyChanged.emit(True)
        self.stateChanged.emit("running")
        self._worker.start()
        return True

    def pause_after_current(self) -> bool:
        if self._worker is None or self._pause_after_current_requested:
            return False
        self._pause_after_current_requested = True
        self._worker.pause_after_current()
        self.stateChanged.emit("pause_after_current")
        return True

    def stop(self) -> bool:
        if self._worker is None:
            return False
        self._worker.cancel()
        return True

    def remove_rows(self, rows: list[int]) -> int:
        return self.model.remove_rows_by_index(rows)

    def retry_rows(self, rows: list[int]) -> int:
        return self.model.retry_rows(rows)

    def clear_completed(self) -> int:
        return self.model.clear_completed()

    def _on_item_started(self, item_id: str, backend: str, encoder: str) -> None:
        self._active_item_ids.add(item_id)
        self.model.assign_backend(item_id, backend, encoder)
        self.model.mark_running(item_id)

    def _on_worker_progress(self, event: dict[str, object]) -> None:
        self.model.apply_progress_event(event)
        self.progress.emit(event)

    def _on_item_finished(self, item_id: str, result: EncodeResult) -> None:
        self._active_item_ids.discard(item_id)
        self.model.apply_result(item_id, result)
        for warning in result.external_subtitle_warnings:
            self.log.emit(warning)

    def _on_worker_paused(self) -> None:
        self.busyChanged.emit(False)
        self.stateChanged.emit("paused")

    def _on_worker_cancelled(self, message: str) -> None:
        for item_id in list(self._active_item_ids):
            self.model.mark_cancelled(item_id, message)
            self._active_item_ids.discard(item_id)
        self.busyChanged.emit(False)
        self.stateChanged.emit("cancelled")

    def _on_worker_failed(self, message: str) -> None:
        for item_id in list(self._active_item_ids):
            self.model.mark_failed(item_id, message)
            self._active_item_ids.discard(item_id)
        self.busyChanged.emit(False)
        self.stateChanged.emit("failed")
        self.error.emit(message)

    def _on_worker_queue_finished(self) -> None:
        self.busyChanged.emit(False)
        self.stateChanged.emit("idle")

    def _on_worker_thread_finished(self) -> None:
        self._worker = None
        self._active_item_ids.clear()
        self._pause_after_current_requested = False
