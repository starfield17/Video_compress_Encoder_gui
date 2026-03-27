from __future__ import annotations

import copy
import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from core.exec_encode import execute_plan_item
from core.models import EncodeResult, OperationCancelledError
from gui.queue_state import QueueItemRecord, create_queue_records
from gui.queue_table import QueueTableModel


@dataclass(slots=True)
class QueueExecutionItem:
    item_id: str
    record: QueueItemRecord


class QueueExecuteWorker(QThread):
    log = Signal(str)
    progress = Signal(object)
    item_started = Signal(str)
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
        self._current_process = None

    def _emit_log(self, message: str) -> None:
        self.log.emit(message)

    def _emit_progress(self, event: dict[str, object]) -> None:
        self.progress.emit(event)

    def _set_current_process(self, proc) -> None:
        self._current_process = proc

    def cancel(self) -> None:
        self._cancel_event.set()
        if self._current_process is not None:
            try:
                self._current_process.terminate()
            except Exception:
                pass

    def pause_after_current(self) -> None:
        self._pause_after_current_event.set()

    def run(self) -> None:
        try:
            for index, item in enumerate(self.items, start=1):
                if self._cancel_event.is_set():
                    raise OperationCancelledError("Encoding cancelled.")
                self.item_started.emit(item.item_id)
                result = execute_plan_item(
                    item.record.job_snapshot.ffmpeg_path,
                    copy.deepcopy(item.record.plan_item),
                    item.record.job_snapshot.workdir,
                    queue_index=index,
                    queue_total=len(self.items),
                    log_callback=self._emit_log,
                    progress_callback=self._emit_progress,
                    cancel_check=self._cancel_event.is_set,
                    process_callback=self._set_current_process,
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
        self._active_item_id: str | None = None
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

    def _on_item_started(self, item_id: str) -> None:
        self._active_item_id = item_id
        self.model.mark_running(item_id)

    def _on_worker_progress(self, event: dict[str, object]) -> None:
        self.model.apply_progress_event(event)
        self.progress.emit(event)

    def _on_item_finished(self, item_id: str, result: EncodeResult) -> None:
        self._active_item_id = None
        self.model.apply_result(item_id, result)
        for warning in result.external_subtitle_warnings:
            self.log.emit(warning)

    def _on_worker_paused(self) -> None:
        self.busyChanged.emit(False)
        self.stateChanged.emit("paused")

    def _on_worker_cancelled(self, message: str) -> None:
        if self._active_item_id:
            self.model.mark_cancelled(self._active_item_id, message)
            self._active_item_id = None
        self.busyChanged.emit(False)
        self.stateChanged.emit("cancelled")

    def _on_worker_failed(self, message: str) -> None:
        if self._active_item_id:
            self.model.mark_failed(self._active_item_id, message)
            self._active_item_id = None
        self.busyChanged.emit(False)
        self.stateChanged.emit("failed")
        self.error.emit(message)

    def _on_worker_queue_finished(self) -> None:
        self.busyChanged.emit(False)
        self.stateChanged.emit("idle")

    def _on_worker_thread_finished(self) -> None:
        self._worker = None
        self._pause_after_current_requested = False
