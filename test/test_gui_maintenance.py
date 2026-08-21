from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from core.i18n import get_translator
from core.models import (
    BackendChoice,
    EncodeOptions,
    EncodePlanItem,
    QualitySearchResult,
    QualitySearchStatus,
    SmartPreviewResult,
)
from gui.gui_mainwindow import MainWindow
from gui.preview_result_dialog import build_preview_summary
from gui.queue_state import (
    QueueItemRecord,
    QueueItemStatus,
    QueueJobSnapshot,
    apply_progress_event,
)


class MainWindowMaintenanceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.repo_root = Path(__file__).resolve().parent.parent

    def test_runtime_config_patch_preserves_worker_owned_and_unknown_fields(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        persisted: dict[str, object] = {}

        def update_config(_config_dir, updater):
            current = {
                "encoder_capabilities": {"source": "worker"},
                "future_config_field": "preserve-me",
            }
            updated = updater(current)
            persisted.update(updated if updated is not None else current)
            return Path("app_config.json")

        try:
            window.app_config["language"] = "zh_cn"
            window.app_config["encoder_capabilities"] = {"source": "stale-window"}
            window.app_config["future_config_field"] = "stale-window"
            with patch("gui.gui_mainwindow.update_app_config", side_effect=update_config):
                window._save_app_config_preserving_capabilities()
        finally:
            window.close()

        self.assertEqual(persisted["language"], "zh_cn")
        self.assertEqual(persisted["encoder_capabilities"], {"source": "worker"})
        self.assertEqual(persisted["future_config_field"], "preserve-me")

    def test_ui_builders_create_the_documented_composition_points(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            self.assertIs(window.centralWidget(), window.main_scroll_area)
            self.assertIsNotNone(window.source_box)
            self.assertIsNotNone(window.options_panel)
            self.assertIsNotNone(window.jobs_box)
            self.assertIsNotNone(window.statusBar())
        finally:
            window.close()

    def test_smart_preview_summary_is_built_without_a_main_window(self) -> None:
        result = SmartPreviewResult(
            source_path=Path("source.mp4"),
            success=True,
            quality_search_result=QualitySearchResult(
                status=QualitySearchStatus.FOUND,
                encoder_name="libx265",
                backend=BackendChoice.CPU,
                selected_video_bitrate_bps=1_500_000,
                min_vmaf=91.25,
                predicted_output_ratio=0.625,
            ),
            log_path=Path("analysis.log"),
        )
        summary = build_preview_summary(
            get_translator("en", self.repo_root / "config"),
            result,
        )
        rendered = "\n".join(summary)
        self.assertIn("1500 kbps", rendered)
        self.assertIn("91.25", rendered)
        self.assertIn("62.50%", rendered)

    def test_queue_progress_transition_is_qt_free(self) -> None:
        item = EncodePlanItem(
            source_path=Path("source.mp4"),
            output_path=Path("output.mp4"),
            media_info=None,
            encoder_info=None,
            options=EncodeOptions(),
        )
        record = QueueItemRecord(
            item_id="item-1",
            plan_item=item,
            job_snapshot=QueueJobSnapshot(
                Path("workdir"),
                Path("ffmpeg"),
                Path("ffprobe"),
                Path("output"),
            ),
            status=QueueItemStatus.WAITING_ANALYSIS,
            total_passes=1,
        )
        apply_progress_event(
            record,
            {
                "state": "analyzing",
                "candidate_index": 2,
                "candidate_limit": 4,
                "file_progress": 37.5,
            },
        )
        self.assertEqual(record.status, QueueItemStatus.ANALYZING)
        self.assertEqual(record.analysis_candidate_index, 2)
        self.assertEqual(record.analysis_candidate_limit, 4)
        self.assertEqual(record.file_progress, 37.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
