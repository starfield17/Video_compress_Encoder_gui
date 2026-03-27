from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QDialog,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.bitrate_policy import human_kbps
from core.i18n import get_translator
from core.models import (
    AudioMode,
    BackendChoice,
    CodecChoice,
    ContainerChoice,
    EncodeOptions,
    PreviewOptions,
    PreviewSampleMode,
)
from core.preset_store import delete_preset, list_presets, load_app_config, load_preset, save_app_config, save_preset
from gui.activity_log_window import ActivityLogWindow
from gui.gui_workers import EncodeWorker, PlanWorker, PreviewWorker
from gui.preview_result_dialog import PreviewResultDialog
from gui.preset_manager_dialog import PresetManagerDialog
from gui.queue_window import QueueWindow
from gui.settings_dialog import SettingsDialog


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{size_bytes} B"


def _format_duration(seconds: float | None) -> str:
    if not seconds:
        return "n/a"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


class MainWindow(QMainWindow):
    def __init__(self, repo_root: Path, language: str | None = None) -> None:
        super().__init__()
        self.repo_root = repo_root
        self.config_dir = repo_root / "config"
        self.default_workdir = repo_root / "workdir"
        self.app_config = load_app_config(self.config_dir)
        self.language = language or self.app_config.get("language", "en")
        self.tr = get_translator(self.language, self.config_dir)
        self.active_worker = None
        self.last_summary_lines: list[str] = []
        self.last_table_rows: list[list[str]] = []

        self.activity_log_window = ActivityLogWindow(self.tr, self)
        self.queue_window = QueueWindow(self.tr, self)

        self._build_ui()
        self._load_initial_state()
        self._apply_translations()
        self._sync_dependent_controls()

    def _build_ui(self) -> None:
        self.setMinimumSize(1280, 820)
        self.resize(1440, 920)

        toolbar = QToolBar(self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        self.toolbar = toolbar

        self.open_source_action = QAction(self)
        self.plan_action = QAction(self)
        self.start_queue_action = QAction(self)
        self.preview_action = QAction(self)
        self.stop_action = QAction(self)
        self.queue_action = QAction(self)
        self.activity_log_action = QAction(self)
        self.presets_action = QAction(self)
        self.settings_action = QAction(self)

        for action in [
            self.open_source_action,
            self.plan_action,
            self.start_queue_action,
            self.preview_action,
            self.stop_action,
            self.queue_action,
            self.activity_log_action,
            self.presets_action,
            self.settings_action,
        ]:
            toolbar.addAction(action)
        self.stop_action.setEnabled(False)

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        source_box = QGroupBox()
        source_layout = QGridLayout(source_box)
        source_layout.setHorizontalSpacing(10)
        source_layout.setVerticalSpacing(10)

        self.source_label = QLabel()
        self.source_combo = QComboBox()
        self.source_combo.setEditable(True)
        self.source_combo.setInsertPolicy(QComboBox.NoInsert)
        self.source_file_button = QPushButton()
        self.source_dir_button = QPushButton()

        self.preset_label = QLabel()
        self.preset_combo = QComboBox()
        self.manage_presets_button = QPushButton()

        self.output_label = QLabel()
        self.output_edit = QLineEdit()
        self.output_button = QPushButton()

        source_layout.addWidget(self.source_label, 0, 0)
        source_layout.addWidget(self.source_combo, 0, 1, 1, 3)
        source_layout.addWidget(self.source_file_button, 0, 4)
        source_layout.addWidget(self.source_dir_button, 0, 5)

        source_layout.addWidget(self.preset_label, 1, 0)
        source_layout.addWidget(self.preset_combo, 1, 1)
        source_layout.addWidget(self.manage_presets_button, 1, 2)
        source_layout.addWidget(self.output_label, 1, 3)
        source_layout.addWidget(self.output_edit, 1, 4)
        source_layout.addWidget(self.output_button, 1, 5)
        source_layout.setColumnStretch(1, 1)
        source_layout.setColumnStretch(4, 1)

        self.options_tabs = QTabWidget()
        self._build_basic_tab()
        self._build_video_tab()
        self._build_audio_tab()
        self._build_preview_tab()
        self._build_advanced_tab()

        jobs_box = QGroupBox()
        jobs_layout = QVBoxLayout(jobs_box)
        jobs_layout.setContentsMargins(12, 12, 12, 12)
        jobs_layout.setSpacing(8)

        self.queue_summary = QTextEdit()
        self.queue_summary.setReadOnly(True)
        self.queue_summary.setFixedHeight(96)

        self.table = QTableWidget(0, 9)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        jobs_layout.addWidget(self.queue_summary)
        jobs_layout.addWidget(self.table, 1)

        root_layout.addWidget(source_box)
        root_layout.addWidget(self.options_tabs)
        root_layout.addWidget(jobs_box, 1)

        self.source_box = source_box
        self.jobs_box = jobs_box

        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)
        self.status_stage_label = QLabel()
        self.status_file_label = QLabel()
        self.status_speed_label = QLabel()
        self.status_elapsed_label = QLabel()
        self.status_progress_label = QLabel("-")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setValue(0)

        status_bar.addWidget(self.status_stage_label, 0)
        status_bar.addWidget(self.status_file_label, 1)
        status_bar.addPermanentWidget(self.status_speed_label)
        status_bar.addPermanentWidget(self.status_elapsed_label)
        status_bar.addPermanentWidget(self.status_progress_label)
        status_bar.addPermanentWidget(self.progress_bar)

        self.open_source_action.triggered.connect(self._browse_source_file)
        self.plan_action.triggered.connect(self._plan)
        self.start_queue_action.triggered.connect(self._encode)
        self.preview_action.triggered.connect(self._preview)
        self.stop_action.triggered.connect(self._stop_active_task)
        self.queue_action.triggered.connect(self._show_queue_window)
        self.activity_log_action.triggered.connect(self._show_activity_log)
        self.presets_action.triggered.connect(self._open_preset_manager)
        self.settings_action.triggered.connect(self._open_settings_dialog)

        self.source_file_button.clicked.connect(self._browse_source_file)
        self.source_dir_button.clicked.connect(self._browse_source_dir)
        self.output_button.clicked.connect(self._browse_output)
        self.manage_presets_button.clicked.connect(self._open_preset_manager)
        self.sample_mode_combo.currentIndexChanged.connect(self._sync_dependent_controls)
        self.audio_mode_combo.currentIndexChanged.connect(self._sync_dependent_controls)
        self.source_combo.editTextChanged.connect(self._persist_runtime_state)
        self.output_edit.editingFinished.connect(self._persist_runtime_state)
        self.preset_combo.currentIndexChanged.connect(self._preset_combo_changed)

    def _build_basic_tab(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.codec_label = QLabel()
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["hevc", "av1"])

        self.backend_label = QLabel()
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["auto", "cpu", "nvenc", "qsv", "amf"])

        self.container_label = QLabel()
        self.container_combo = QComboBox()
        self.container_combo.addItems(["mkv", "mp4"])

        self.ratio_label = QLabel()
        self.ratio_edit = QLineEdit()

        self.overwrite_check = QCheckBox()
        self.recursive_check = QCheckBox()

        layout.addWidget(self.codec_label, 0, 0)
        layout.addWidget(self.codec_combo, 0, 1)
        layout.addWidget(self.backend_label, 0, 2)
        layout.addWidget(self.backend_combo, 0, 3)
        layout.addWidget(self.container_label, 0, 4)
        layout.addWidget(self.container_combo, 0, 5)
        layout.addWidget(self.ratio_label, 1, 0)
        layout.addWidget(self.ratio_edit, 1, 1)
        layout.addWidget(self.overwrite_check, 2, 0, 1, 2)
        layout.addWidget(self.recursive_check, 2, 2, 1, 2)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(5, 1)

        self.options_tabs.addTab(page, "")
        self.basic_tab = page

    def _build_video_tab(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.encoder_preset_label = QLabel()
        self.encoder_preset_edit = QLineEdit()

        self.pix_fmt_label = QLabel()
        self.pix_fmt_edit = QLineEdit()

        self.min_bitrate_label = QLabel()
        self.min_bitrate_spin = QSpinBox()
        self.min_bitrate_spin.setRange(0, 500000)
        self.min_bitrate_spin.setValue(250)

        self.max_bitrate_label = QLabel()
        self.max_bitrate_spin = QSpinBox()
        self.max_bitrate_spin.setRange(0, 500000)
        self.max_bitrate_spin.setValue(0)

        self.maxrate_factor_label = QLabel()
        self.maxrate_factor_spin = QDoubleSpinBox()
        self.maxrate_factor_spin.setRange(0.1, 20.0)
        self.maxrate_factor_spin.setDecimals(2)
        self.maxrate_factor_spin.setSingleStep(0.05)
        self.maxrate_factor_spin.setValue(1.08)

        self.bufsize_factor_label = QLabel()
        self.bufsize_factor_spin = QDoubleSpinBox()
        self.bufsize_factor_spin.setRange(0.1, 20.0)
        self.bufsize_factor_spin.setDecimals(2)
        self.bufsize_factor_spin.setSingleStep(0.10)
        self.bufsize_factor_spin.setValue(2.0)

        self.two_pass_check = QCheckBox()

        layout.addWidget(self.encoder_preset_label, 0, 0)
        layout.addWidget(self.encoder_preset_edit, 0, 1)
        layout.addWidget(self.pix_fmt_label, 0, 2)
        layout.addWidget(self.pix_fmt_edit, 0, 3)
        layout.addWidget(self.min_bitrate_label, 1, 0)
        layout.addWidget(self.min_bitrate_spin, 1, 1)
        layout.addWidget(self.max_bitrate_label, 1, 2)
        layout.addWidget(self.max_bitrate_spin, 1, 3)
        layout.addWidget(self.maxrate_factor_label, 2, 0)
        layout.addWidget(self.maxrate_factor_spin, 2, 1)
        layout.addWidget(self.bufsize_factor_label, 2, 2)
        layout.addWidget(self.bufsize_factor_spin, 2, 3)
        layout.addWidget(self.two_pass_check, 3, 0, 1, 2)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        self.options_tabs.addTab(page, "")
        self.video_tab = page

    def _build_audio_tab(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.audio_mode_label = QLabel()
        self.audio_mode_combo = QComboBox()
        self.audio_mode_combo.addItems(["copy", "aac"])

        self.audio_bitrate_label = QLabel()
        self.audio_bitrate_edit = QLineEdit()

        self.copy_subtitles_check = QCheckBox()
        self.copy_external_subtitles_check = QCheckBox()

        layout.addWidget(self.audio_mode_label, 0, 0)
        layout.addWidget(self.audio_mode_combo, 0, 1)
        layout.addWidget(self.audio_bitrate_label, 0, 2)
        layout.addWidget(self.audio_bitrate_edit, 0, 3)
        layout.addWidget(self.copy_subtitles_check, 1, 0, 1, 2)
        layout.addWidget(self.copy_external_subtitles_check, 1, 2, 1, 2)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        self.options_tabs.addTab(page, "")
        self.audio_tab = page

    def _build_preview_tab(self) -> None:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.sample_mode_label = QLabel()
        self.sample_mode_combo = QComboBox()
        self.sample_mode_combo.addItems(["middle", "custom"])

        self.sample_duration_label = QLabel()
        self.sample_duration_spin = QDoubleSpinBox()
        self.sample_duration_spin.setRange(1.0, 3600.0)
        self.sample_duration_spin.setDecimals(1)
        self.sample_duration_spin.setValue(30.0)

        self.sample_start_label = QLabel()
        self.sample_start_spin = QDoubleSpinBox()
        self.sample_start_spin.setRange(0.0, 86400.0)
        self.sample_start_spin.setDecimals(1)
        self.sample_start_spin.setValue(0.0)

        layout.addWidget(self.sample_mode_label, 0, 0)
        layout.addWidget(self.sample_mode_combo, 0, 1)
        layout.addWidget(self.sample_duration_label, 0, 2)
        layout.addWidget(self.sample_duration_spin, 0, 3)
        layout.addWidget(self.sample_start_label, 1, 0)
        layout.addWidget(self.sample_start_spin, 1, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        self.options_tabs.addTab(page, "")
        self.preview_tab = page

    def _build_advanced_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.advanced_info = QLabel()
        self.advanced_info.setWordWrap(True)
        layout.addWidget(self.advanced_info)
        layout.addStretch(1)

        self.options_tabs.addTab(page, "")
        self.advanced_tab = page

    def _load_initial_state(self) -> None:
        self._set_source_history(list(self.app_config.get("recent_paths", [])))
        self.source_combo.setEditText(self.app_config.get("last_source_path", ""))
        self.output_edit.setText(self.app_config.get("last_output_dir", ""))

        self._refresh_presets()
        default_preset = self.app_config.get("default_preset_name")
        if default_preset:
            preset_index = self.preset_combo.findText(default_preset)
            if preset_index >= 0:
                self.preset_combo.setCurrentIndex(preset_index)
                try:
                    self._apply_options(load_preset(default_preset, self.config_dir))
                except FileNotFoundError:
                    self._apply_options(EncodeOptions())
            else:
                self._apply_options(EncodeOptions())
        else:
            self._apply_options(EncodeOptions())

        self._set_summary([self.tr.t("gui.summary.idle")])
        self._set_status_snapshot("-", "-", "-", "-", 0.0)

    def _apply_translations(self) -> None:
        self.setWindowTitle(self.tr.t("app.title"))
        self.source_box.setTitle(self.tr.t("gui.group.source"))
        self.jobs_box.setTitle(self.tr.t("gui.group.jobs"))

        self.open_source_action.setText(self.tr.t("gui.button.open_source"))
        self.plan_action.setText(self.tr.t("gui.button.add_to_queue"))
        self.start_queue_action.setText(self.tr.t("gui.button.start_queue"))
        self.preview_action.setText(self.tr.t("gui.button.preview"))
        self.stop_action.setText(self.tr.t("gui.button.stop"))
        self.queue_action.setText(self.tr.t("gui.button.queue"))
        self.activity_log_action.setText(self.tr.t("gui.button.activity_log"))
        self.presets_action.setText(self.tr.t("gui.button.presets"))
        self.settings_action.setText(self.tr.t("gui.button.settings"))

        self.source_label.setText(self.tr.t("gui.label.source"))
        self.preset_label.setText(self.tr.t("gui.label.preset"))
        self.output_label.setText(self.tr.t("gui.label.output"))
        self.source_file_button.setText(self.tr.t("gui.button.browse_file"))
        self.source_dir_button.setText(self.tr.t("gui.button.browse_dir"))
        self.output_button.setText(self.tr.t("gui.button.browse_dir"))
        self.manage_presets_button.setText(self.tr.t("gui.button.manage_presets"))

        self.codec_label.setText(self.tr.t("gui.label.codec"))
        self.backend_label.setText(self.tr.t("gui.label.backend"))
        self.container_label.setText(self.tr.t("gui.label.container"))
        self.ratio_label.setText(self.tr.t("gui.label.ratio"))
        self.overwrite_check.setText(self.tr.t("gui.checkbox.overwrite"))
        self.recursive_check.setText(self.tr.t("gui.checkbox.recursive"))
        self.encoder_preset_label.setText(self.tr.t("gui.label.encoder_preset"))
        self.pix_fmt_label.setText(self.tr.t("gui.label.pix_fmt"))
        self.min_bitrate_label.setText(self.tr.t("gui.label.min_video_kbps"))
        self.max_bitrate_label.setText(self.tr.t("gui.label.max_video_kbps"))
        self.maxrate_factor_label.setText(self.tr.t("gui.label.maxrate_factor"))
        self.bufsize_factor_label.setText(self.tr.t("gui.label.bufsize_factor"))
        self.two_pass_check.setText(self.tr.t("gui.checkbox.two_pass"))
        self.audio_mode_label.setText(self.tr.t("gui.label.audio_mode"))
        self.audio_bitrate_label.setText(self.tr.t("gui.label.audio_bitrate"))
        self.copy_subtitles_check.setText(self.tr.t("gui.checkbox.copy_subtitles"))
        self.copy_external_subtitles_check.setText(self.tr.t("gui.checkbox.copy_external_subtitles"))
        self.sample_mode_label.setText(self.tr.t("gui.label.sample_mode"))
        self.sample_duration_label.setText(self.tr.t("gui.label.sample_duration"))
        self.sample_start_label.setText(self.tr.t("gui.label.sample_start"))
        self.advanced_info.setText(self.tr.t("gui.advanced.placeholder"))

        self.source_combo.lineEdit().setPlaceholderText(self.tr.t("gui.placeholder.source"))
        self.output_edit.setPlaceholderText(self.tr.t("gui.placeholder.default_output"))
        self.ratio_edit.setPlaceholderText(self.tr.t("gui.placeholder.auto_ratio"))
        self.encoder_preset_edit.setPlaceholderText(self.tr.t("gui.placeholder.encoder_preset"))
        self.pix_fmt_edit.setPlaceholderText("yuv420p")
        self.audio_bitrate_edit.setPlaceholderText("128k")

        self.options_tabs.setTabText(self.options_tabs.indexOf(self.basic_tab), self.tr.t("gui.tab.basic"))
        self.options_tabs.setTabText(self.options_tabs.indexOf(self.video_tab), self.tr.t("gui.tab.video"))
        self.options_tabs.setTabText(self.options_tabs.indexOf(self.audio_tab), self.tr.t("gui.tab.audio_subtitles"))
        self.options_tabs.setTabText(self.options_tabs.indexOf(self.preview_tab), self.tr.t("gui.tab.preview"))
        self.options_tabs.setTabText(self.options_tabs.indexOf(self.advanced_tab), self.tr.t("gui.tab.advanced"))

        self.status_stage_label.setText(self.tr.t("gui.statusbar.stage", value="-"))
        self.status_file_label.setText(self.tr.t("gui.statusbar.file", value="-"))
        self.status_speed_label.setText(self.tr.t("gui.statusbar.speed", value="-"))
        self.status_elapsed_label.setText(self.tr.t("gui.statusbar.elapsed", value="-"))
        self.status_progress_label.setText("-")

        self.table.setHorizontalHeaderLabels(
            [
                self.tr.t("gui.table.source"),
                self.tr.t("gui.table.resolution"),
                self.tr.t("gui.table.duration"),
                self.tr.t("gui.table.source_bitrate"),
                self.tr.t("gui.table.target_bitrate"),
                self.tr.t("gui.table.encoder"),
                self.tr.t("gui.table.output"),
                self.tr.t("gui.table.note"),
                self.tr.t("gui.table.status"),
            ]
        )

        self.activity_log_window.apply_translations(self.tr)
        self.queue_window.apply_translations(self.tr)
        if self.last_summary_lines:
            self._set_summary(self.last_summary_lines)
        if self.last_table_rows:
            self.queue_window.set_rows(self.last_table_rows)

    def _set_source_history(self, items: list[str]) -> None:
        current = self.source_combo.currentText().strip()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems(items)
        self.source_combo.setEditText(current)
        self.source_combo.blockSignals(False)

    def _append_log(self, message: str) -> None:
        self.activity_log_window.append_message(message)
        self.statusBar().showMessage(message, 5000)

    def _set_summary(self, lines: list[str]) -> None:
        self.last_summary_lines = lines[:]
        self.queue_summary.setPlainText("\n".join(lines))
        self.queue_window.set_summary_lines(lines)

    def _set_status_snapshot(
        self,
        stage: str,
        file_name: str,
        speed: str,
        elapsed: str,
        percent: float | None,
    ) -> None:
        self.status_stage_label.setText(self.tr.t("gui.statusbar.stage", value=stage))
        self.status_file_label.setText(self.tr.t("gui.statusbar.file", value=file_name))
        self.status_speed_label.setText(self.tr.t("gui.statusbar.speed", value=speed))
        self.status_elapsed_label.setText(self.tr.t("gui.statusbar.elapsed", value=elapsed))
        if percent is None:
            self.status_progress_label.setText("-")
        else:
            bounded = max(0.0, min(100.0, percent))
            self.status_progress_label.setText(f"{bounded:.1f}%")
            self.progress_bar.setValue(int(round(bounded * 10)))

    def _language_changed(self, language: str) -> None:
        self.language = language
        self.tr = get_translator(self.language, self.config_dir)
        self.app_config["language"] = self.language
        self._persist_runtime_state()
        self._apply_translations()
        self._sync_dependent_controls()

    def _browse_source_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr.t("gui.dialog.select_source_file"))
        if path:
            self.source_combo.setEditText(path)
            self._persist_runtime_state()

    def _browse_source_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_source_dir"))
        if path:
            self.source_combo.setEditText(path)
            self._persist_runtime_state()

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_output_dir"))
        if path:
            self.output_edit.setText(path)
            self._persist_runtime_state()

    def _refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("")
        self.preset_combo.addItems(list_presets(self.config_dir))
        index = self.preset_combo.findText(current)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

    def _preset_combo_changed(self) -> None:
        self._persist_runtime_state()
        name = self.preset_combo.currentText().strip()
        if not name:
            return
        try:
            self._apply_options(load_preset(name, self.config_dir))
        except Exception as exc:
            QMessageBox.critical(self, self.tr.t("gui.message.error"), str(exc))
            return
        self._append_log(self.tr.t("gui.log.preset_loaded", name=name))

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.tr, self.app_config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        old_language = self.language
        self.app_config.update(values)
        self._persist_runtime_state()
        if values["language"] != old_language:
            self._language_changed(str(values["language"]))

    def _open_preset_manager(self) -> None:
        dialog = PresetManagerDialog(
            self.tr,
            list_presets(self.config_dir),
            str(self.app_config.get("default_preset_name", "")),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_action:
            return

        action = dialog.selected_action["action"]
        name = dialog.selected_action["name"]

        try:
            if action == "load":
                options = load_preset(name, self.config_dir)
                self._apply_options(options)
                self.preset_combo.setCurrentText(name)
                self._append_log(self.tr.t("gui.log.preset_loaded", name=name))
            elif action == "save":
                path = save_preset(name, self._current_options(), self.config_dir)
                self.app_config["default_preset_name"] = name
                self._refresh_presets()
                self.preset_combo.setCurrentText(name)
                self._append_log(self.tr.t("gui.log.preset_saved", name=name, path=path))
            elif action == "delete":
                delete_preset(name, self.config_dir)
                if self.app_config.get("default_preset_name") == name:
                    self.app_config["default_preset_name"] = ""
                self._refresh_presets()
                self._append_log(self.tr.t("gui.log.preset_deleted", name=name))
            elif action == "set_default":
                self.app_config["default_preset_name"] = name
                self.preset_combo.setCurrentText(name)
                self._append_log(self.tr.t("gui.log.default_preset_set", name=name))
            self._persist_runtime_state()
        except Exception as exc:
            QMessageBox.critical(self, self.tr.t("gui.message.error"), str(exc))

    def _current_options(self) -> EncodeOptions:
        ratio_text = self.ratio_edit.text().strip()
        encoder_preset = self.encoder_preset_edit.text().strip() or None
        pix_fmt = self.pix_fmt_edit.text().strip() or "yuv420p"
        return EncodeOptions(
            codec=CodecChoice(self.codec_combo.currentText()),
            backend=BackendChoice(self.backend_combo.currentText()),
            ratio=float(ratio_text) if ratio_text else None,
            min_video_kbps=int(self.min_bitrate_spin.value()),
            max_video_kbps=int(self.max_bitrate_spin.value()),
            container=ContainerChoice(self.container_combo.currentText()),
            audio_mode=AudioMode(self.audio_mode_combo.currentText()),
            audio_bitrate=self.audio_bitrate_edit.text().strip() or "128k",
            copy_subtitles=self.copy_subtitles_check.isChecked(),
            copy_external_subtitles=self.copy_external_subtitles_check.isChecked(),
            two_pass=self.two_pass_check.isChecked(),
            encoder_preset=encoder_preset,
            pix_fmt=pix_fmt,
            maxrate_factor=float(self.maxrate_factor_spin.value()),
            bufsize_factor=float(self.bufsize_factor_spin.value()),
            overwrite=self.overwrite_check.isChecked(),
            recursive=self.recursive_check.isChecked(),
        )

    def _apply_options(self, options: EncodeOptions) -> None:
        self.codec_combo.setCurrentText(options.codec.value)
        self.backend_combo.setCurrentText(options.backend.value)
        self.ratio_edit.setText("" if options.ratio is None else str(options.ratio))
        self.container_combo.setCurrentText(options.container.value)
        self.audio_mode_combo.setCurrentText(options.audio_mode.value)
        self.audio_bitrate_edit.setText(options.audio_bitrate)
        self.encoder_preset_edit.setText(options.encoder_preset or "")
        self.pix_fmt_edit.setText(options.pix_fmt)
        self.min_bitrate_spin.setValue(options.min_video_kbps)
        self.max_bitrate_spin.setValue(options.max_video_kbps)
        self.maxrate_factor_spin.setValue(options.maxrate_factor)
        self.bufsize_factor_spin.setValue(options.bufsize_factor)
        self.copy_subtitles_check.setChecked(options.copy_subtitles)
        self.copy_external_subtitles_check.setChecked(options.copy_external_subtitles)
        self.two_pass_check.setChecked(options.two_pass)
        self.overwrite_check.setChecked(options.overwrite)
        self.recursive_check.setChecked(options.recursive)
        self._sync_dependent_controls()

    def _selected_input(self) -> Path | None:
        text = self.source_combo.currentText().strip()
        return Path(text).expanduser().resolve() if text else None

    def _selected_output(self) -> Path | None:
        text = self.output_edit.text().strip()
        return Path(text).expanduser().resolve() if text else None

    def _selected_workdir(self) -> Path:
        text = str(self.app_config.get("workdir_path", "")).strip()
        return Path(text).expanduser().resolve() if text else self.default_workdir.resolve()

    def _selected_ffmpeg(self) -> str | None:
        text = str(self.app_config.get("ffmpeg_path", "")).strip()
        return text or None

    def _selected_ffprobe(self) -> str | None:
        text = str(self.app_config.get("ffprobe_path", "")).strip()
        return text or None

    def _sync_dependent_controls(self) -> None:
        custom_sample = self.sample_mode_combo.currentText() == PreviewSampleMode.CUSTOM.value
        self.sample_start_spin.setEnabled(custom_sample)
        self.audio_bitrate_edit.setEnabled(self.audio_mode_combo.currentText() == AudioMode.AAC.value)

    def _persist_runtime_state(self) -> None:
        source_text = self.source_combo.currentText().strip()
        output_text = self.output_edit.text().strip()
        self.app_config["language"] = self.language
        self.app_config["last_source_path"] = source_text
        self.app_config["last_output_dir"] = output_text
        self.app_config.setdefault("workdir_path", str(self.default_workdir))
        self.app_config.setdefault("ffmpeg_path", "")
        self.app_config.setdefault("ffprobe_path", "")
        self.app_config.setdefault("keep_preview_temp", True)
        self.app_config.setdefault("log_level", "info")

        recent_paths = list(self.app_config.get("recent_paths", []))
        if source_text:
            recent_paths = [item for item in recent_paths if item != source_text]
            recent_paths.insert(0, source_text)
            self.app_config["recent_paths"] = recent_paths[:10]
            self._set_source_history(self.app_config["recent_paths"])
        save_app_config(self.config_dir, self.app_config)

    def _set_busy(self, busy: bool) -> None:
        for action in [
            self.open_source_action,
            self.plan_action,
            self.start_queue_action,
            self.preview_action,
            self.presets_action,
            self.settings_action,
        ]:
            action.setEnabled(not busy)
        self.stop_action.setEnabled(busy)

        for widget in [
            self.source_file_button,
            self.source_dir_button,
            self.output_button,
            self.manage_presets_button,
            self.source_combo,
            self.preset_combo,
            self.output_edit,
            self.options_tabs,
        ]:
            widget.setEnabled(not busy)

    def _start_worker(self, worker) -> None:
        self.active_worker = worker
        self._set_busy(True)
        if hasattr(worker, "log"):
            worker.log.connect(self._append_log)
        if hasattr(worker, "progress"):
            worker.progress.connect(self._update_progress)
        worker.finished.connect(lambda: self._set_busy(False))
        worker.finished.connect(lambda: setattr(self, "active_worker", None))
        worker.failed.connect(self._on_worker_failed)
        if hasattr(worker, "cancelled"):
            worker.cancelled.connect(self._on_worker_cancelled)
        worker.start()

    def _on_worker_failed(self, message: str) -> None:
        self._append_log(f"{self.tr.t('gui.message.error')}: {message}")
        self._set_status_snapshot(self.tr.t("gui.status.failed"), "-", "-", "-", None)
        QMessageBox.critical(self, self.tr.t("gui.message.error"), message)

    def _on_worker_cancelled(self, message: str) -> None:
        self._append_log(message)
        self._set_summary(
            [
                self.tr.t("gui.summary.mode", mode=self.tr.t("gui.button.stop")),
                self.tr.t("gui.summary.cancelled", message=message),
            ]
        )
        self._set_status_snapshot(self.tr.t("gui.status.cancelled"), "-", "-", "-", None)

    def _stop_active_task(self) -> None:
        if self.active_worker is None or not hasattr(self.active_worker, "cancel"):
            return
        self._append_log(self.tr.t("gui.log.stop_requested"))
        self.active_worker.cancel()
        self.stop_action.setEnabled(False)

    def _update_progress(self, event: dict[str, object]) -> None:
        stage = str(event.get("stage") or event.get("phase") or "-")
        state = str(event.get("state") or "-")
        file_name = str(event.get("file_name") or event.get("file_path") or "-")
        percent = event.get("percent")
        speed = str(event.get("speed") or "-")
        elapsed_sec = event.get("elapsed_sec")
        duration_sec = event.get("duration_sec")

        if isinstance(elapsed_sec, (int, float)):
            elapsed_text = _format_duration(float(elapsed_sec))
            if isinstance(duration_sec, (int, float)) and float(duration_sec) > 0:
                elapsed_text = f"{elapsed_text} / {_format_duration(float(duration_sec))}"
        elif isinstance(duration_sec, (int, float)) and float(duration_sec) > 0:
            elapsed_text = _format_duration(float(duration_sec))
        else:
            elapsed_text = "-"

        bounded_percent = None
        if isinstance(percent, (int, float)):
            bounded_percent = max(0.0, min(100.0, float(percent)))

        self._set_status_snapshot(f"{stage} / {state}", file_name, speed if speed else "-", elapsed_text, bounded_percent)

    def _build_context(self) -> tuple[Path, EncodeOptions, Path | None, Path, str | None, str | None]:
        input_path = self._selected_input()
        if input_path is None:
            raise ValueError(self.tr.t("gui.message.select_source"))
        options = self._current_options()
        output_dir = self._selected_output()
        workdir = self._selected_workdir()
        ffmpeg_path = self._selected_ffmpeg()
        ffprobe_path = self._selected_ffprobe()
        self._persist_runtime_state()
        return input_path, options, output_dir, workdir, ffmpeg_path, ffprobe_path

    def _plan(self) -> None:
        try:
            input_path, options, output_dir, workdir, ffmpeg_path, ffprobe_path = self._build_context()
        except Exception as exc:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), str(exc))
            return

        self._append_log(self.tr.t("gui.log.planning"))
        self.progress_bar.setValue(0)
        self.status_progress_label.setText("0.0%")
        worker = PlanWorker(
            input_path=input_path,
            options=options,
            output_dir=output_dir,
            workdir=workdir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
        worker.completed.connect(self._on_plan_ready)
        self._start_worker(worker)

    def _preview(self) -> None:
        try:
            input_path, options, output_dir, workdir, ffmpeg_path, ffprobe_path = self._build_context()
        except Exception as exc:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), str(exc))
            return

        if not input_path.is_file():
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), self.tr.t("gui.message.preview_requires_file"))
            return

        preview_options = PreviewOptions(
            sample_mode=PreviewSampleMode(self.sample_mode_combo.currentText()),
            sample_duration_sec=float(self.sample_duration_spin.value()),
            custom_start_sec=(
                float(self.sample_start_spin.value())
                if self.sample_mode_combo.currentText() == PreviewSampleMode.CUSTOM.value
                else None
            ),
        )
        self._append_log(self.tr.t("gui.log.previewing"))
        self.progress_bar.setValue(0)
        self.status_progress_label.setText("0.0%")
        worker = PreviewWorker(
            input_path=input_path,
            options=options,
            preview_options=preview_options,
            output_dir=output_dir,
            workdir=workdir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
        worker.completed.connect(self._on_preview_ready)
        self._start_worker(worker)

    def _encode(self) -> None:
        try:
            input_path, options, output_dir, workdir, ffmpeg_path, ffprobe_path = self._build_context()
        except Exception as exc:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), str(exc))
            return

        self._append_log(self.tr.t("gui.log.encoding"))
        self.progress_bar.setValue(0)
        self.status_progress_label.setText("0.0%")
        worker = EncodeWorker(
            input_path=input_path,
            options=options,
            output_dir=output_dir,
            workdir=workdir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
        worker.completed.connect(self._on_encode_ready)
        self._start_worker(worker)

    def _show_queue_window(self) -> None:
        self.queue_window.show()
        self.queue_window.raise_()
        self.queue_window.activateWindow()

    def _show_activity_log(self) -> None:
        self.activity_log_window.show()
        self.activity_log_window.raise_()
        self.activity_log_window.activateWindow()

    def _on_plan_ready(self, plan) -> None:
        self._populate_table(plan)
        valid_items = [item for item in plan.items if not item.skip_reason]
        skipped_items = [item for item in plan.items if item.skip_reason]
        summary = [
            self.tr.t("gui.summary.mode", mode=self.tr.t("gui.button.add_to_queue")),
            self.tr.t("gui.summary.plan_count", total=len(plan.items), ready=len(valid_items), skipped=len(skipped_items)),
            self.tr.t("gui.summary.output_root", path=plan.output_root),
            self.tr.t("gui.summary.ffmpeg", path=plan.ffmpeg_path),
            self.tr.t("gui.summary.ffprobe", path=plan.ffprobe_path),
        ]
        self._set_summary(summary)
        self._append_log(self.tr.t("gui.log.plan_ready"))
        self._set_status_snapshot(self.tr.t("gui.status.done"), "-", "-", "-", 100.0)

    def _on_preview_ready(self, result) -> None:
        if result.success:
            summary = [
                self.tr.t("gui.summary.mode", mode=self.tr.t("gui.button.preview")),
                self.tr.t("gui.summary.preview_source", path=result.job.source_path),
                self.tr.t("gui.summary.preview_window", start=result.job.start_sec, duration=result.job.duration_sec),
                self.tr.t("gui.summary.preview_source_sample", path=result.job.source_sample_path),
                self.tr.t("gui.summary.preview_encoded_sample", path=result.job.encoded_sample_path),
                self.tr.t("gui.summary.preview_ratio", value=f"{result.sample_compression_ratio:.3f}"),
                self.tr.t("gui.summary.preview_estimated_size", value=_format_size(result.estimated_full_output_size)),
                self.tr.t("gui.summary.log_path", path=result.log_path or ""),
            ]
            self._set_summary(summary)
            self._append_log(self.tr.t("gui.log.preview_done"))
            self._append_log(
                self.tr.t(
                    "gui.log.preview_ratio",
                    ratio=f"{result.sample_compression_ratio:.3f}",
                    size=_format_size(result.estimated_full_output_size),
                )
            )
            dialog = PreviewResultDialog(self.tr, summary[1:], self)
            dialog.exec()
            self._set_status_snapshot(self.tr.t("gui.status.done"), result.job.source_path.name, "-", "-", 100.0)
            return

        self._append_log(f"{self.tr.t('gui.message.error')}: {result.error_message}")
        QMessageBox.critical(self, self.tr.t("gui.message.error"), result.error_message or "Preview failed.")

    def _on_encode_ready(self, payload) -> None:
        plan, results = payload
        self._populate_table(plan, results)
        success_count = sum(1 for result in results if result.success)
        skipped_count = sum(1 for result in results if result.skipped)
        failed_count = sum(1 for result in results if not result.success and not result.skipped)
        summary = [
            self.tr.t("gui.summary.mode", mode=self.tr.t("gui.button.start_queue")),
            self.tr.t("gui.summary.encode_count", success=success_count, skipped=skipped_count, failed=failed_count),
            self.tr.t("gui.summary.output_root", path=plan.output_root),
            self.tr.t("gui.summary.ffmpeg", path=plan.ffmpeg_path),
            self.tr.t("gui.summary.ffprobe", path=plan.ffprobe_path),
        ]
        self._set_summary(summary)
        self._append_log(self.tr.t("gui.log.encode_done"))
        self._set_status_snapshot(self.tr.t("gui.status.done"), "-", "-", "-", 100.0)

    def _build_table_rows(self, plan, results=None) -> list[list[str]]:
        result_map = {str(result.source_path): result for result in results or []}
        rows: list[list[str]] = []
        for item in plan.items:
            media = item.media_info
            encoder = item.encoder_info
            note = item.skip_reason or "; ".join(item.warnings) or ""
            status = self.tr.t("gui.status.ready")
            if item.skip_reason:
                status = self.tr.t("gui.status.skip")

            result = result_map.get(str(item.source_path))
            if result:
                if result.skipped:
                    status = self.tr.t("gui.status.skip")
                elif result.success:
                    status = self.tr.t("gui.status.done")
                else:
                    status = self.tr.t("gui.status.failed")
                    note = result.error_message or note

            resolution = (
                f"{media.width}x{media.height}" if media and media.width and media.height else "n/a"
            )
            duration = _format_duration(media.duration if media else None)
            rows.append(
                [
                    str(item.source_path),
                    resolution,
                    duration,
                    human_kbps(media.video_bitrate_bps) if media else "n/a",
                    human_kbps(item.target_video_bitrate_bps) if item.target_video_bitrate_bps else "n/a",
                    f"{encoder.encoder_name} ({encoder.backend.value})" if encoder else "n/a",
                    str(item.output_path),
                    note,
                    status,
                ]
            )
        return rows

    def _populate_table(self, plan, results=None) -> None:
        rows = self._build_table_rows(plan, results)
        self.last_table_rows = rows
        self.table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for col_index, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row_index, col_index, cell)
        self.queue_window.set_rows(rows)
