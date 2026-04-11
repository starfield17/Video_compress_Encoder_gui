from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from cli.cli_entry import run_cli
from core.bitrate_policy import DEFAULT_RATIO
from core.build_ffmpeg_cmd import build_video_args
from core.encoder_caps import default_preset_for_encoder, is_valid_preset, preset_choices_for_encoder
from core.models import (
    BackendChoice,
    CodecChoice,
    ContainerChoice,
    EncodeOptions,
    EncodePlanItem,
    EncoderInfo,
    MediaInfo,
    VideoFileItem,
)
from core.plan_encode import build_encode_plan
from core.preset_store import preset_data_to_encode_options
from gui.gui_mainwindow import MainWindow


def _media(path: Path) -> MediaInfo:
    return MediaInfo(
        path=path,
        duration=12.0,
        format_bitrate_bps=5_000_000,
        video_bitrate_bps=4_000_000,
        audio_bitrate_bps=128_000,
        width=1920,
        height=1080,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
    )


def _encoder_info(encoder_name: str, backend: BackendChoice, default_preset: str | None) -> EncoderInfo:
    return EncoderInfo(
        codec=CodecChoice.HEVC,
        backend=backend,
        encoder_name=encoder_name,
        supports_two_pass=encoder_name == "libx265",
        default_preset=default_preset,
    )


def _plan_item(encoder_name: str, backend: BackendChoice, options: EncodeOptions | None = None) -> EncodePlanItem:
    current = options or EncodeOptions()
    source = Path("/tmp/source.mp4")
    return EncodePlanItem(
        source_path=source,
        output_path=Path("/tmp/output.mkv"),
        media_info=_media(source),
        encoder_info=_encoder_info(encoder_name, backend, current.encoder_preset),
        options=current,
        target_video_bitrate_bps=2_000_000,
    )


class EncoderCapsTestCase(unittest.TestCase):
    def test_default_presets_are_quality_tuned(self) -> None:
        self.assertEqual(default_preset_for_encoder("libx265"), "slow")
        self.assertEqual(default_preset_for_encoder("libsvtav1"), "5")
        self.assertEqual(default_preset_for_encoder("hevc_nvenc"), "p6")
        self.assertEqual(default_preset_for_encoder("av1_nvenc"), "p6")
        self.assertEqual(default_preset_for_encoder("hevc_qsv"), "slow")
        self.assertEqual(default_preset_for_encoder("av1_qsv"), "slow")

    def test_fallback_preset_lists_match_expected(self) -> None:
        ffmpeg_path = Path("/tmp/fake_ffmpeg")
        with patch("core.encoder_caps._cached_runtime_preset_choices", return_value=()):
            self.assertEqual(
                preset_choices_for_encoder(ffmpeg_path, "libx265"),
                ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"],
            )
            self.assertEqual(preset_choices_for_encoder(ffmpeg_path, "hevc_nvenc"), ["p1", "p2", "p3", "p4", "p5", "p6", "p7"])
            self.assertEqual(preset_choices_for_encoder(ffmpeg_path, "av1_nvenc"), ["p1", "p2", "p3", "p4", "p5", "p6", "p7"])
            self.assertEqual(
                preset_choices_for_encoder(ffmpeg_path, "hevc_qsv"),
                ["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
            )
            self.assertEqual(
                preset_choices_for_encoder(ffmpeg_path, "av1_qsv"),
                ["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
            )
            self.assertEqual(preset_choices_for_encoder(ffmpeg_path, "libsvtav1"), [])

    def test_is_valid_preset_uses_detected_choices(self) -> None:
        ffmpeg_path = Path("/tmp/fake_ffmpeg")
        with patch("core.encoder_caps._cached_runtime_preset_choices", return_value=("p5", "p6")):
            self.assertTrue(is_valid_preset(ffmpeg_path, "hevc_nvenc", "p6"))
            self.assertFalse(is_valid_preset(ffmpeg_path, "hevc_nvenc", "slow"))


class PlanningAndCommandTestCase(unittest.TestCase):
    def test_default_ratios_are_relaxed(self) -> None:
        self.assertEqual(DEFAULT_RATIO[CodecChoice.HEVC], 0.76)
        self.assertEqual(DEFAULT_RATIO[CodecChoice.AV1], 0.64)

    def test_build_encode_plan_injects_default_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "sample.mp4"
            source.write_text("x", encoding="utf-8")
            options = EncodeOptions(copy_external_subtitles=False, overwrite=True)
            encoder_info = _encoder_info("hevc_nvenc", BackendChoice.NVENC, "p6")
            with (
                patch("core.plan_encode.discover_ffmpeg_tools", return_value=(temp_root / "ffmpeg", temp_root / "ffprobe")),
                patch("core.plan_encode.list_available_encoders", return_value={"hevc_nvenc"}),
                patch("core.plan_encode.resolve_encoder", return_value=encoder_info),
                patch("core.plan_encode.preset_choices_for_encoder", return_value=["p5", "p6"]),
                patch("core.plan_encode.is_valid_preset", return_value=True),
                patch("core.plan_encode.probe_media_info", return_value=_media(source)),
                patch("core.plan_encode.validate_plan_item"),
            ):
                plan = build_encode_plan(
                    input_path=None,
                    options=options,
                    output_dir=temp_root / "out",
                    workdir=temp_root,
                    files=[VideoFileItem(path=source, relative_path=Path(source.name))],
                )
            self.assertEqual(plan.items[0].options.encoder_preset, "p6")

    def test_invalid_default_preset_falls_back_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "sample.mp4"
            source.write_text("x", encoding="utf-8")
            options = EncodeOptions(copy_external_subtitles=False, overwrite=True)
            encoder_info = _encoder_info("hevc_nvenc", BackendChoice.NVENC, "p6")
            with (
                patch("core.plan_encode.discover_ffmpeg_tools", return_value=(temp_root / "ffmpeg", temp_root / "ffprobe")),
                patch("core.plan_encode.list_available_encoders", return_value={"hevc_nvenc"}),
                patch("core.plan_encode.resolve_encoder", return_value=encoder_info),
                patch("core.plan_encode.preset_choices_for_encoder", return_value=["p5"]),
                patch("core.plan_encode.is_valid_preset", return_value=False),
                patch("core.plan_encode.probe_media_info", return_value=_media(source)),
                patch("core.plan_encode.validate_plan_item"),
            ):
                plan = build_encode_plan(
                    input_path=None,
                    options=options,
                    output_dir=temp_root / "out",
                    workdir=temp_root,
                    files=[VideoFileItem(path=source, relative_path=Path(source.name))],
                )
            self.assertIsNone(plan.items[0].options.encoder_preset)

    def test_build_video_args_keeps_svt_without_vbv(self) -> None:
        item = _plan_item(
            "libsvtav1",
            BackendChoice.CPU,
            EncodeOptions(codec=CodecChoice.AV1, encoder_preset="5"),
        )
        item.encoder_info = _encoder_info("libsvtav1", BackendChoice.CPU, "5")
        args = build_video_args(item)
        self.assertNotIn("-maxrate", args)
        self.assertNotIn("-bufsize", args)
        self.assertIn("-preset", args)

    def test_build_video_args_uses_new_default_vbv_factors(self) -> None:
        item = _plan_item("hevc_nvenc", BackendChoice.NVENC, EncodeOptions())
        item.encoder_info = _encoder_info("hevc_nvenc", BackendChoice.NVENC, "p6")
        args = build_video_args(item)
        self.assertIn("-maxrate", args)
        self.assertIn("2500000", args)
        self.assertIn("-bufsize", args)
        self.assertIn("8000000", args)

    def test_legacy_empty_string_preset_loads_as_none(self) -> None:
        restored = preset_data_to_encode_options(
            {
                "codec": "hevc",
                "backend": "auto",
                "parallel_enabled": False,
                "parallel_backends": [],
                "ratio": None,
                "min_video_kbps": 250,
                "max_video_kbps": 0,
                "container": "mp4",
                "audio_mode": "copy",
                "audio_bitrate": "128k",
                "copy_subtitles": True,
                "copy_external_subtitles": False,
                "two_pass": False,
                "preset": "",
                "pix_fmt": "yuv420p",
                "maxrate_factor": 1.25,
                "bufsize_factor": 4.0,
            }
        )
        self.assertIsNone(restored.encoder_preset)


class GuiPresetSelectionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.repo_root = Path(__file__).resolve().parent.parent

    def test_gui_shows_nvenc_and_qsv_preset_choices(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            with (
                patch("gui.gui_mainwindow.discover_ffmpeg_tools", return_value=(Path("/tmp/ffmpeg"), Path("/tmp/ffprobe"))),
                patch("gui.gui_mainwindow.list_available_encoders", return_value={"hevc_nvenc", "hevc_qsv"}),
                patch(
                    "gui.gui_mainwindow.resolve_encoder",
                    side_effect=lambda codec, backend, available, ffmpeg_path=None: _encoder_info(
                        "hevc_nvenc" if backend == BackendChoice.NVENC else "hevc_qsv",
                        backend,
                        "p6" if backend == BackendChoice.NVENC else "slow",
                    ),
                ),
                patch(
                    "gui.gui_mainwindow.preset_choices_for_encoder",
                    side_effect=lambda ffmpeg_path, encoder_name: ["p1", "p2", "p3"] if encoder_name == "hevc_nvenc" else ["veryfast", "fast", "slow"],
                ),
            ):
                window.backend_combo.setCurrentText("nvenc")
                window._refresh_encoder_preset_choices()
                self.assertEqual(window.encoder_preset_combo.itemText(1), "p1")
                window.backend_combo.setCurrentText("qsv")
                window._refresh_encoder_preset_choices()
                self.assertEqual(window.encoder_preset_combo.itemText(1), "veryfast")
        finally:
            window.close()

    def test_gui_auto_backend_uses_default_only_and_disables_combo(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            window.backend_combo.setCurrentText("auto")
            window._refresh_encoder_preset_choices()
            self.assertEqual(window.encoder_preset_combo.count(), 1)
            self.assertFalse(window.encoder_preset_combo.isEnabled())
        finally:
            window.close()

    def test_gui_invalid_loaded_preset_falls_back_to_default(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            with (
                patch("gui.gui_mainwindow.discover_ffmpeg_tools", return_value=(Path("/tmp/ffmpeg"), Path("/tmp/ffprobe"))),
                patch("gui.gui_mainwindow.list_available_encoders", return_value={"hevc_nvenc"}),
                patch(
                    "gui.gui_mainwindow.resolve_encoder",
                    return_value=_encoder_info("hevc_nvenc", BackendChoice.NVENC, "p6"),
                ),
                patch("gui.gui_mainwindow.preset_choices_for_encoder", return_value=["p5", "p6"]),
            ):
                window._apply_options(EncodeOptions(backend=BackendChoice.NVENC, encoder_preset="invalid"))
                self.assertIsNone(window.encoder_preset_combo.currentData())
        finally:
            window.close()


class CliPresetValidationTestCase(unittest.TestCase):
    def test_cli_accepts_valid_preset(self) -> None:
        with (
            patch("cli.cli_entry.discover_ffmpeg_tools", return_value=(Path("/tmp/ffmpeg"), Path("/tmp/ffprobe"))),
            patch("cli.cli_entry.list_available_encoders", return_value={"hevc_nvenc"}),
            patch("cli.cli_entry.resolve_encoder", return_value=_encoder_info("hevc_nvenc", BackendChoice.NVENC, "p6")),
            patch("cli.cli_entry.preset_choices_for_encoder", return_value=["p5", "p6"]),
            patch("cli.cli_entry.build_encode_plan") as plan_mock,
            patch("cli.cli_entry.print_plan"),
        ):
            plan_mock.return_value = type("Plan", (), {"items": [], "warnings": []})()
            exit_code = run_cli(["plan", "input.mp4", "--backend", "nvenc", "--encoder-preset", "p6"])
        self.assertEqual(exit_code, 0)

    def test_cli_rejects_invalid_preset(self) -> None:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            patch("cli.cli_entry.discover_ffmpeg_tools", return_value=(Path("/tmp/ffmpeg"), Path("/tmp/ffprobe"))),
            patch("cli.cli_entry.list_available_encoders", return_value={"hevc_nvenc"}),
            patch("cli.cli_entry.resolve_encoder", return_value=_encoder_info("hevc_nvenc", BackendChoice.NVENC, "p6")),
            patch("cli.cli_entry.preset_choices_for_encoder", return_value=["p5", "p6"]),
        ):
            exit_code = run_cli(["plan", "input.mp4", "--backend", "nvenc", "--encoder-preset", "slow"])
        self.assertEqual(exit_code, 2)
        self.assertIn("hevc_nvenc", stderr.getvalue())
        self.assertIn("p5, p6", stderr.getvalue())

    def test_cli_auto_backend_resolves_encoder_before_validation(self) -> None:
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            patch("cli.cli_entry.discover_ffmpeg_tools", return_value=(Path("/tmp/ffmpeg"), Path("/tmp/ffprobe"))),
            patch("cli.cli_entry.list_available_encoders", return_value={"hevc_nvenc"}),
            patch("cli.cli_entry.resolve_encoder", return_value=_encoder_info("hevc_nvenc", BackendChoice.NVENC, "p6")) as resolve_mock,
            patch("cli.cli_entry.preset_choices_for_encoder", return_value=["p5", "p6"]),
        ):
            exit_code = run_cli(["plan", "input.mp4", "--backend", "auto", "--encoder-preset", "slow"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(resolve_mock.call_args.args[1], BackendChoice.AUTO)
        self.assertIn("hevc_nvenc", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
