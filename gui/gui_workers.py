from __future__ import annotations

from pathlib import Path

try:
    from PySide6.QtCore import QThread, Signal
except ImportError:
    from PySide2.QtCore import QThread, Signal

from core.exec_encode import execute_plan, execute_preview
from core.models import EncodeOptions, PreviewOptions
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

    def run(self) -> None:
        try:
            plan = build_encode_plan(
                input_path=self.input_path,
                options=self.options,
                output_dir=self.output_dir,
                workdir=self.workdir,
                ffmpeg_path=self.ffmpeg_path,
                ffprobe_path=self.ffprobe_path,
            )
            self.completed.emit(plan)
        except Exception as exc:
            self.failed.emit(str(exc))


class PreviewWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

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

    def run(self) -> None:
        try:
            plan = build_encode_plan(
                input_path=self.input_path,
                options=self.options,
                output_dir=self.output_dir,
                workdir=self.workdir,
                ffmpeg_path=self.ffmpeg_path,
                ffprobe_path=self.ffprobe_path,
            )
            item = next((item for item in plan.items if not item.skip_reason), None)
            if item is None:
                raise RuntimeError("No valid plan item is available for preview.")
            job = build_preview_job(item, self.workdir, self.preview_options)
            result = execute_preview(job, plan.ffmpeg_path, self.workdir)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class EncodeWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

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

    def run(self) -> None:
        try:
            plan = build_encode_plan(
                input_path=self.input_path,
                options=self.options,
                output_dir=self.output_dir,
                workdir=self.workdir,
                ffmpeg_path=self.ffmpeg_path,
                ffprobe_path=self.ffprobe_path,
            )
            results = execute_plan(plan, self.workdir)
            self.completed.emit((plan, results))
        except Exception as exc:
            self.failed.emit(str(exc))

