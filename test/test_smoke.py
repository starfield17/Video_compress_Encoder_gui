from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QHeaderView

from core.app_paths import app_root, config_dir
from core.i18n import get_translator
from core.models import EncodeOptions
from core.plan_encode import build_encode_plan
from core.preset_store import app_config_path
from gui.gui_mainwindow import MainWindow
from gui.queue_manager import QueueManager
from gui.queue_table import QueueColumn, QueueTableModel, create_queue_view
from main import main


class SmokeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = app_root()
        oceans_video = cls.repo_root / "workdir" / "oceans.mp4"
        cls.sample_video = oceans_video if oceans_video.exists() else cls.repo_root / "workdir" / "test.mp4"
        cls.app = QApplication.instance() or QApplication([])

    def require_sample_video(self) -> None:
        if not self.sample_video.exists():
            self.skipTest(f"Sample video is missing: {self.sample_video}")

    def test_app_config_path_uses_workdir(self) -> None:
        config_path = app_config_path(config_dir())
        self.assertEqual(config_path, self.repo_root / "workdir" / "app_config.json")

    def test_oceans_video_is_used_as_smoke_sample_when_present(self) -> None:
        if not (self.repo_root / "workdir" / "oceans.mp4").exists():
            self.skipTest("oceans.mp4 is not present in workdir")
        self.assertEqual(self.sample_video.name, "oceans.mp4")

    def test_cli_plan_smoke(self) -> None:
        self.require_sample_video()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["--cli", "plan", str(self.sample_video), "--overwrite"])
        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Plan items:", output)
        self.assertIn(str(self.sample_video), output)

    def test_queue_metrics_after_plan_add(self) -> None:
        self.require_sample_video()
        plan = build_encode_plan(
            input_path=self.sample_video,
            options=EncodeOptions(overwrite=True),
            workdir=self.repo_root / "workdir",
        )
        model = QueueTableModel(get_translator("en", self.repo_root / "config"))
        manager = QueueManager(model)
        added = manager.add_plan(plan, self.repo_root / "workdir")
        metrics = model.metrics()
        self.assertEqual(added, 1)
        self.assertEqual(metrics.total_items, 1)
        self.assertEqual(metrics.ready_items, 1)
        self.assertGreater(metrics.total_duration_sec, 0.0)
        self.assertEqual(metrics.queue_percent, 0.0)

    def test_main_window_offscreen_init(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            self.assertEqual(window.queue_model.rowCount(), 0)
            self.assertFalse(window.queue_busy)
            self.assertEqual(window.queue_progress_bar.value(), 0)
        finally:
            window.close()

    def test_main_window_applies_desktop_polish_contract(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            self.assertIn("VideoCompressorTheme", window.styleSheet())
            self.assertEqual(window.toolbar.toolButtonStyle(), Qt.ToolButtonTextBesideIcon)
            for action in [
                window.add_files_action,
                window.add_folder_action,
                window.plan_action,
                window.start_queue_action,
                window.pause_after_current_action,
                window.stop_action,
                window.preview_action,
                window.queue_action,
                window.activity_log_action,
                window.presets_action,
                window.settings_action,
            ]:
                self.assertFalse(action.icon().isNull(), action.text())
                self.assertTrue(action.toolTip(), action.text())
                self.assertTrue(action.statusTip(), action.text())
            for label in [
                window.total_items_title,
                window.total_duration_title,
                window.states_title,
                window.saved_space_title,
            ]:
                self.assertEqual(label.objectName(), "summaryTitle")
            for label in [
                window.total_items_value,
                window.total_duration_value,
                window.states_value,
                window.saved_space_value,
            ]:
                self.assertEqual(label.objectName(), "summaryValue")
        finally:
            window.close()

    def test_responsive_queue_view_fills_viewport_when_space_is_available(self) -> None:
        model = QueueTableModel(get_translator("en", self.repo_root / "config"))
        view = create_queue_view()
        view.setModel(model)
        view.resize(1700, 420)
        view.show()
        try:
            self.app.processEvents()
            self.app.processEvents()
            header = view.horizontalHeader()
            actual_total = sum(
                header.sectionSize(column)
                for column in range(model.columnCount())
                if not view.isColumnHidden(column)
            )
            viewport_width = view.viewport().width()
            self.assertLessEqual(abs(actual_total - viewport_width), 1)
        finally:
            view.close()

    def test_flex_columns_are_user_resizable(self) -> None:
        view = create_queue_view()
        model = QueueTableModel(get_translator("en", self.repo_root / "config"))
        view.setModel(model)
        try:
            header = view.horizontalHeader()
            self.assertEqual(header.sectionResizeMode(int(QueueColumn.NAME)), QHeaderView.Interactive)
            self.assertEqual(header.sectionResizeMode(int(QueueColumn.FOLDER)), QHeaderView.Interactive)
            self.assertEqual(header.sectionResizeMode(int(QueueColumn.RESOLUTION)), QHeaderView.Fixed)
        finally:
            view.close()

    def test_manual_resize_survives_reflow(self) -> None:
        model = QueueTableModel(get_translator("en", self.repo_root / "config"))
        view = create_queue_view()
        view.setModel(model)
        view.resize(1700, 420)
        view.show()
        try:
            self.app.processEvents()
            header = view.horizontalHeader()
            target_width = header.sectionSize(int(QueueColumn.NAME)) + 90
            header.resizeSection(int(QueueColumn.NAME), target_width)
            self.app.processEvents()
            self.app.processEvents()
            self.assertGreaterEqual(header.sectionSize(int(QueueColumn.NAME)), target_width)
        finally:
            view.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
