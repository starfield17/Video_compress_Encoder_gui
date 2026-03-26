from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.exec_encode import execute_plan, execute_preview
from core.models import EncodeOptions, OperationCancelledError, PreviewOptions
from core.plan_encode import build_encode_plan
from core.preview_sample import build_preview_job
from core.scan_videos import collect_video_files


class ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, input_path: Path, recursive: bool) -> None:
        super().__init__()
        self.input_path = input_path
        self.recursive = recursive

    def run(self) -> None:
        try:
            self.completed.emit(collect_video_files(self.input_path, self.recursive))
        except Exception as exc:
            self.failed.emit(str(exc))


class PlanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    log = Signal(str)
    progress = Signal(object)

    def __init__(
        self,
        input_path: Path,
        options: EncodeOptions,
        output_dir: Path | None,
        workdir: Path,
        ffmpeg_path: str | None,
        ffprobe_path: str | None,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.options = options
        self.output_dir = output_dir
        self.workdir = workdir
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._cancel_event = threading.Event()

    def _emit_log(self, message: str) -> None:
        self.log.emit(message)
        print(message, file=sys.stdout, flush=True)

    def _emit_progress(self, event: dict[str, object]) -> None:
        self.progress.emit(event)

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            plan = build_encode_plan(
                input_path=self.input_path,
                options=self.options,
                output_dir=self.output_dir,
                workdir=self.workdir,
                ffmpeg_path=self.ffmpeg_path,
                ffprobe_path=self.ffprobe_path,
                progress_callback=self._emit_log,
                progress_event_callback=self._emit_progress,
                cancel_check=self._cancel_event.is_set,
            )
            self.completed.emit(plan)
        except OperationCancelledError as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class PreviewWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    log = Signal(str)
    progress = Signal(object)

    def __init__(
        self,
        input_path: Path,
        options: EncodeOptions,
        preview_options: PreviewOptions,
        output_dir: Path | None,
        workdir: Path,
        ffmpeg_path: str | None,
        ffprobe_path: str | None,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.options = options
        self.preview_options = preview_options
        self.output_dir = output_dir
        self.workdir = workdir
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._cancel_event = threading.Event()
        self._current_process = None

    def _emit_log(self, message: str) -> None:
        self.log.emit(message)
        print(message, file=sys.stdout, flush=True)

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

    def run(self) -> None:
        try:
            plan = build_encode_plan(
                input_path=self.input_path,
                options=self.options,
                output_dir=self.output_dir,
                workdir=self.workdir,
                ffmpeg_path=self.ffmpeg_path,
                ffprobe_path=self.ffprobe_path,
                progress_callback=self._emit_log,
                progress_event_callback=self._emit_progress,
                cancel_check=self._cancel_event.is_set,
            )
            item = next((item for item in plan.items if not item.skip_reason), None)
            if item is None:
                raise RuntimeError("No valid plan item is available for preview.")
            job = build_preview_job(item, self.workdir, self.preview_options)
            result = execute_preview(
                job,
                plan.ffmpeg_path,
                self.workdir,
                log_callback=self._emit_log,
                progress_callback=self._emit_progress,
                cancel_check=self._cancel_event.is_set,
                process_callback=self._set_current_process,
            )
            self.completed.emit(result)
        except OperationCancelledError as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class EncodeWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    log = Signal(str)
    progress = Signal(object)

    def __init__(
        self,
        input_path: Path,
        options: EncodeOptions,
        output_dir: Path | None,
        workdir: Path,
        ffmpeg_path: str | None,
        ffprobe_path: str | None,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.options = options
        self.output_dir = output_dir
        self.workdir = workdir
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._cancel_event = threading.Event()
        self._current_process = None

    def _emit_log(self, message: str) -> None:
        self.log.emit(message)
        print(message, file=sys.stdout, flush=True)

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

    def run(self) -> None:
        try:
            plan = build_encode_plan(
                input_path=self.input_path,
                options=self.options,
                output_dir=self.output_dir,
                workdir=self.workdir,
                ffmpeg_path=self.ffmpeg_path,
                ffprobe_path=self.ffprobe_path,
                progress_callback=self._emit_log,
                progress_event_callback=self._emit_progress,
                cancel_check=self._cancel_event.is_set,
            )
            results = execute_plan(
                plan,
                self.workdir,
                log_callback=self._emit_log,
                progress_callback=self._emit_progress,
                cancel_check=self._cancel_event.is_set,
                process_callback=self._set_current_process,
            )
            self.completed.emit((plan, results))
        except OperationCancelledError as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
