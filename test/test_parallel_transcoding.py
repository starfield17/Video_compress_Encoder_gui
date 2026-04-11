from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from cli.cli_entry import _build_parser, _merge_options, _parse_parallel_backends, run_cli
from core.models import (
    AudioMode,
    BackendChoice,
    CodecChoice,
    ContainerChoice,
    EncodeOptions,
    EncodePlan,
    EncodePlanItem,
    EncodeResult,
    EncoderInfo,
    MediaInfo,
)
from core.parallel_queue_exec import execute_plan_parallel
from core.preset_store import encode_options_to_preset_data, preset_data_to_encode_options
from gui.gui_mainwindow import MainWindow
from gui.queue_state import create_queue_records
from gui.queue_table import QueueColumn, QueueTableModel


def _media(path: Path) -> MediaInfo:
    return MediaInfo(
        path=path,
        duration=10.0,
        format_bitrate_bps=4_000_000,
        video_bitrate_bps=3_000_000,
        audio_bitrate_bps=128_000,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
    )


def _encoder(backend: BackendChoice) -> EncoderInfo:
    names = {
        BackendChoice.CPU: "libx265",
        BackendChoice.NVENC: "hevc_nvenc",
        BackendChoice.QSV: "hevc_qsv",
        BackendChoice.AMF: "hevc_amf",
    }
    defaults = {
        BackendChoice.CPU: "slow",
        BackendChoice.NVENC: "p6",
        BackendChoice.QSV: "slow",
        BackendChoice.AMF: None,
    }
    return EncoderInfo(
        codec=CodecChoice.HEVC,
        backend=backend,
        encoder_name=names[backend],
        supports_two_pass=backend == BackendChoice.CPU,
        default_preset=defaults[backend],
    )


def _plan(tmp: Path, count: int = 4, options: EncodeOptions | None = None) -> EncodePlan:
    current = options or EncodeOptions(overwrite=True)
    items: list[EncodePlanItem] = []
    for index in range(count):
        source = tmp / f"video_{index}.mp4"
        items.append(
            EncodePlanItem(
                source_path=source,
                output_path=tmp / f"video_{index}.mkv",
                media_info=_media(source),
                encoder_info=_encoder(BackendChoice.NVENC),
                options=current,
                target_video_bitrate_bps=2_000_000,
            )
        )
    return EncodePlan(
        items=items,
        ffmpeg_path=tmp / "ffmpeg",
        ffprobe_path=tmp / "ffprobe",
        input_root=tmp,
        output_root=tmp / "out",
    )


class ParallelPresetTestCase(unittest.TestCase):
    def test_parallel_fields_round_trip(self) -> None:
        options = EncodeOptions(
            parallel_enabled=True,
            parallel_backends=(BackendChoice.NVENC, BackendChoice.QSV),
        )
        data = encode_options_to_preset_data(options)
        restored = preset_data_to_encode_options(data)
        self.assertTrue(restored.parallel_enabled)
        self.assertEqual(restored.parallel_backends, (BackendChoice.NVENC, BackendChoice.QSV))

    def test_old_preset_defaults_parallel_fields(self) -> None:
        data = {
            "codec": "hevc",
            "backend": "auto",
            "ratio": None,
            "min_video_kbps": 250,
            "max_video_kbps": 0,
            "container": "mp4",
            "audio_mode": "copy",
            "audio_bitrate": "128k",
            "copy_subtitles": True,
            "copy_external_subtitles": False,
            "two_pass": False,
            "preset": None,
            "pix_fmt": "yuv420p",
            "maxrate_factor": 1.08,
            "bufsize_factor": 2.0,
        }
        restored = preset_data_to_encode_options(data)
        self.assertFalse(restored.parallel_enabled)
        self.assertEqual(restored.parallel_backends, ())


class ParallelSchedulerTestCase(unittest.TestCase):
    def test_parallel_scheduler_uses_multiple_backends_and_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            plan = _plan(temp_root)
            consumed: list[tuple[str, str]] = []
            lock = threading.Lock()

            def fake_execute(_ffmpeg, item, _workdir, **_kwargs):
                time.sleep(0.02 if item.options.backend == BackendChoice.NVENC else 0.01)
                with lock:
                    consumed.append((item.source_path.name, item.options.backend.value))
                return EncodeResult(
                    source_path=item.source_path,
                    output_path=item.output_path,
                    success=True,
                )

            with (
                patch("core.parallel_queue_exec.list_available_encoders", return_value={"hevc_nvenc", "hevc_qsv"}),
                patch("core.parallel_queue_exec.resolve_encoder", side_effect=lambda codec, backend, available, ffmpeg_path=None: _encoder(backend)),
                patch("core.parallel_queue_exec.execute_plan_item", side_effect=fake_execute),
            ):
                results = execute_plan_parallel(
                    plan,
                    temp_root,
                    backends=(BackendChoice.NVENC, BackendChoice.QSV),
                )

            self.assertEqual(len(results), len(plan.items))
            self.assertEqual([result.source_path.name for result in results], [item.source_path.name for item in plan.items])
            self.assertEqual({backend for _, backend in consumed}, {"nvenc", "qsv"})

    def test_parallel_scheduler_stops_on_worker_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            plan = _plan(temp_root, count=5)
            finished: list[str] = []

            def fake_execute(_ffmpeg, item, _workdir, **_kwargs):
                if item.source_path.name == "video_1.mp4" and item.options.backend == BackendChoice.QSV:
                    raise RuntimeError("boom")
                time.sleep(0.01)
                return EncodeResult(
                    source_path=item.source_path,
                    output_path=item.output_path,
                    success=True,
                )

            with (
                patch("core.parallel_queue_exec.list_available_encoders", return_value={"hevc_nvenc", "hevc_qsv"}),
                patch("core.parallel_queue_exec.resolve_encoder", side_effect=lambda codec, backend, available, ffmpeg_path=None: _encoder(backend)),
                patch("core.parallel_queue_exec.execute_plan_item", side_effect=fake_execute),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    execute_plan_parallel(
                        plan,
                        temp_root,
                        backends=(BackendChoice.NVENC, BackendChoice.QSV),
                        item_result_callback=lambda index, result: finished.append(plan.items[index].source_path.name),
                    )

            self.assertLess(len(finished), len(plan.items))


class ParallelCliTestCase(unittest.TestCase):
    def test_parse_parallel_backends(self) -> None:
        self.assertEqual(
            _parse_parallel_backends("nvenc, qsv, nvenc"),
            (BackendChoice.NVENC, BackendChoice.QSV),
        )
        self.assertEqual(_parse_parallel_backends(""), ())

    def test_merge_options_sets_parallel_fields(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["encode", "input.mp4", "--parallel", "--parallel-backends", "nvenc,qsv"])
        options = _merge_options(EncodeOptions(), args)
        self.assertTrue(options.parallel_enabled)
        self.assertEqual(options.parallel_backends, (BackendChoice.NVENC, BackendChoice.QSV))

    def test_parallel_requires_backends(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = run_cli(["encode", "input.mp4", "--parallel"])
        self.assertEqual(exit_code, 2)
        self.assertIn("requires at least one backend", stderr.getvalue())

    def test_parallel_rejects_two_pass(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = run_cli(["encode", "input.mp4", "--parallel", "--parallel-backends", "nvenc,qsv", "--two-pass"])
        self.assertEqual(exit_code, 2)
        self.assertIn("does not support two-pass", stderr.getvalue())

    def test_parallel_rejects_manual_preset(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = run_cli(
                ["encode", "input.mp4", "--parallel", "--parallel-backends", "nvenc,qsv", "--encoder-preset", "slow"]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("does not support a manual encoder preset", stderr.getvalue())

    def test_parallel_encode_dispatches_to_parallel_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            plan = _plan(
                temp_root,
                count=1,
                options=EncodeOptions(parallel_enabled=True, parallel_backends=(BackendChoice.NVENC, BackendChoice.QSV)),
            )
            result = EncodeResult(source_path=plan.items[0].source_path, output_path=plan.items[0].output_path, success=True)
            with (
                patch("cli.cli_entry.build_encode_plan", return_value=plan),
                patch("cli.cli_entry.execute_plan_parallel", return_value=[result]) as parallel_mock,
                patch("cli.cli_entry.print_plan"),
                patch("cli.cli_entry.print_encode_results"),
            ):
                exit_code = run_cli(["encode", "input.mp4", "--parallel", "--parallel-backends", "nvenc,qsv"])
            self.assertEqual(exit_code, 0)
            parallel_mock.assert_called_once()


class ParallelGuiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.repo_root = Path(__file__).resolve().parent.parent

    def test_window_collects_parallel_options_and_syncs_controls(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            window.parallel_check.setChecked(True)
            window.parallel_nvenc_check.setChecked(True)
            window.parallel_qsv_check.setChecked(True)
            window._sync_dependent_controls()
            options = window._current_options()
            self.assertTrue(options.parallel_enabled)
            self.assertEqual(options.parallel_backends, (BackendChoice.NVENC, BackendChoice.QSV))
            self.assertFalse(window.backend_combo.isEnabled())
            self.assertTrue(window.parallel_nvenc_check.isEnabled())
        finally:
            window.close()

    def test_gui_parallel_validation_rejects_invalid_combinations(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            with self.assertRaisesRegex(ValueError, "requires at least one backend"):
                window._validate_parallel_options_for_gui(EncodeOptions(parallel_enabled=True))
            with self.assertRaisesRegex(ValueError, "does not support two-pass"):
                window._validate_parallel_options_for_gui(
                    EncodeOptions(parallel_enabled=True, parallel_backends=(BackendChoice.NVENC,), two_pass=True)
                )
            with self.assertRaisesRegex(ValueError, "does not support a manual encoder preset"):
                window._validate_parallel_options_for_gui(
                    EncodeOptions(
                        parallel_enabled=True,
                        parallel_backends=(BackendChoice.NVENC,),
                        encoder_preset="slow",
                    )
                )
        finally:
            window.close()

    def test_queue_table_prefers_runtime_assigned_encoder(self) -> None:
        model = QueueTableModel(window_tr := MainWindow(self.repo_root, language="en").tr)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_root = Path(temp_dir)
                plan = _plan(temp_root, count=1)
                records = create_queue_records(plan, temp_root)
                model.add_records(records)
                model.assign_backend(records[0].item_id, "qsv", "hevc_qsv")
                index = model.index(0, int(QueueColumn.ENCODER))
                self.assertEqual(model.data(index), "hevc_qsv (qsv)")
        finally:
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
