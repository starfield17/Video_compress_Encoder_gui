from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.i18n import get_translator
from core.models import (
    AudioMode,
    BackendChoice,
    CodecChoice,
    ContainerChoice,
    EncodeOptions,
    PreviewOptions,
    PreviewSampleMode,
    VideoFileItem,
)
from core.preset_store import delete_preset, list_presets, load_app_config, load_preset, save_app_config, save_preset
from gui.activity_log_window import ActivityLogWindow
from gui.gui_workers import PlanWorker, PreviewWorker
from gui.preview_result_dialog import PreviewResultDialog
from gui.preset_manager_dialog import PresetManagerDialog
from gui.queue_manager import QueueManager
from gui.queue_table import QueueTableModel, create_queue_view, format_duration, format_size
from gui.queue_window import QueueWindow
from gui.settings_dialog import SettingsDialog


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
        self.queue_busy = False
        self._header_sync_guard = False
        self._queue_state = "idle"
        self._status_stage = "-"
        self._status_file = "-"
        self._status_speed = "-"
        self._status_elapsed = "-"
        self._status_percent: float | None = None

        self.queue_model = QueueTableModel(self.tr, self)
        self.queue_manager = QueueManager(self.queue_model, self)
        self.activity_log_window = ActivityLogWindow(self.tr, self)
        self.queue_window = QueueWindow(self.tr, self.queue_model, self)

        self._build_ui()
        self._connect_signals()
        self._load_initial_state()
        self._apply_translations()
        self._sync_dependent_controls()
        self._update_queue_metrics(self.queue_model.metrics())
        self._refresh_action_state()

    def _build_ui(self) -> None:
        self.setMinimumSize(1360, 860)
        self.resize(1520, 960)

        toolbar = QToolBar(self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        self.toolbar = toolbar

        self.add_files_action = QAction(self)
        self.add_folder_action = QAction(self)
        self.plan_action = QAction(self)
        self.start_queue_action = QAction(self)
        self.pause_after_current_action = QAction(self)
        self.preview_action = QAction(self)
        self.stop_action = QAction(self)
        self.queue_action = QAction(self)
        self.activity_log_action = QAction(self)
        self.presets_action = QAction(self)
        self.settings_action = QAction(self)

        for action in [
            self.add_files_action,
            self.add_folder_action,
            self.plan_action,
            self.start_queue_action,
            self.pause_after_current_action,
            self.stop_action,
        ]:
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.preview_action)
        toolbar.addSeparator()
        for action in [
            self.queue_action,
            self.activity_log_action,
            self.presets_action,
            self.settings_action,
        ]:
            toolbar.addAction(action)

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

        summary_widget = QWidget()
        summary_layout = QHBoxLayout(summary_widget)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(12)

        left_summary = QWidget()
        left_layout = QGridLayout(left_summary)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setHorizontalSpacing(10)
        left_layout.setVerticalSpacing(6)

        right_summary = QWidget()
        right_layout = QGridLayout(right_summary)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setHorizontalSpacing(10)
        right_layout.setVerticalSpacing(6)

        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setFrameShadow(QFrame.Sunken)

        self.total_items_title = QLabel()
        self.total_items_value = QLabel("-")
        self.total_duration_title = QLabel()
        self.total_duration_value = QLabel("-")
        self.states_title = QLabel()
        self.states_value = QLabel("-")
        self.saved_space_title = QLabel()
        self.saved_space_value = QLabel("-")

        left_layout.addWidget(self.total_items_title, 0, 0)
        left_layout.addWidget(self.total_items_value, 0, 1)
        left_layout.addWidget(self.total_duration_title, 1, 0)
        left_layout.addWidget(self.total_duration_value, 1, 1)
        left_layout.setColumnStretch(1, 1)

        right_layout.addWidget(self.states_title, 0, 0)
        right_layout.addWidget(self.states_value, 0, 1)
        right_layout.addWidget(self.saved_space_title, 1, 0)
        right_layout.addWidget(self.saved_space_value, 1, 1)
        right_layout.setColumnStretch(1, 1)

        summary_layout.addWidget(left_summary, 1)
        summary_layout.addWidget(divider)
        summary_layout.addWidget(right_summary, 1)

        self.queue_progress_text = QLabel()
        self.queue_progress_bar = QProgressBar()
        self.queue_progress_bar.setRange(0, 1000)

        self.table_view = create_queue_view(self)
        self.table_view.setModel(self.queue_model)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)

        jobs_layout.addWidget(summary_widget)
        jobs_layout.addWidget(self.queue_progress_text)
        jobs_layout.addWidget(self.queue_progress_bar)
        jobs_layout.addWidget(self.table_view, 1)

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
        self.status_current_progress_label = QLabel()
        self.current_progress_bar = QProgressBar()
        self.current_progress_bar.setRange(0, 1000)
        self.current_progress_bar.setFixedWidth(220)
        self.current_progress_bar.setValue(0)

        status_bar.addWidget(self.status_stage_label, 0)
        status_bar.addWidget(self.status_file_label, 1)
        status_bar.addPermanentWidget(self.status_speed_label)
        status_bar.addPermanentWidget(self.status_elapsed_label)
        status_bar.addPermanentWidget(self.status_current_progress_label)
        status_bar.addPermanentWidget(self.current_progress_bar)

    def _connect_signals(self) -> None:
        self.add_files_action.triggered.connect(self._add_files_dialog)
        self.add_folder_action.triggered.connect(self._add_folder_dialog)
        self.plan_action.triggered.connect(self._plan_current_source)
        self.start_queue_action.triggered.connect(self._start_queue)
        self.pause_after_current_action.triggered.connect(self._pause_after_current)
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
        self.parallel_check.toggled.connect(self._sync_dependent_controls)
        self.source_combo.editTextChanged.connect(self._persist_runtime_state)
        self.output_edit.editingFinished.connect(self._persist_runtime_state)
        self.preset_combo.currentIndexChanged.connect(self._preset_combo_changed)

        self.queue_model.metricsChanged.connect(self._update_queue_metrics)
        self.queue_manager.log.connect(self._append_log)
        self.queue_manager.progress.connect(self._update_progress)
        self.queue_manager.busyChanged.connect(self._on_queue_busy_changed)
        self.queue_manager.stateChanged.connect(self._on_queue_state_changed)
        self.queue_manager.error.connect(self._on_queue_error)

        self.table_view.customContextMenuRequested.connect(lambda pos: self._show_queue_context_menu(self.table_view, pos))
        self.queue_window.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_window.table_view.customContextMenuRequested.connect(
            lambda pos: self._show_queue_context_menu(self.queue_window.table_view, pos)
        )
        self.table_view.horizontalHeader().customContextMenuRequested.connect(
            lambda pos: self._show_header_context_menu(self.table_view, pos)
        )
        self.queue_window.table_view.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_window.table_view.horizontalHeader().customContextMenuRequested.connect(
            lambda pos: self._show_header_context_menu(self.queue_window.table_view, pos)
        )

        for view in [self.table_view, self.queue_window.table_view]:
            header = view.horizontalHeader()
            header.sectionMoved.connect(lambda *_args, source_view=view: self._persist_header_state(source_view))

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
        self.parallel_check = QCheckBox()
        self.parallel_backends_label = QLabel()
        self.parallel_nvenc_check = QCheckBox("NVENC")
        self.parallel_qsv_check = QCheckBox("QSV")
        self.parallel_amf_check = QCheckBox("AMF")
        self.parallel_cpu_check = QCheckBox("CPU")

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
        layout.addWidget(self.parallel_check, 3, 0, 1, 2)
        layout.addWidget(self.parallel_backends_label, 4, 0)
        layout.addWidget(self.parallel_nvenc_check, 4, 1)
        layout.addWidget(self.parallel_qsv_check, 4, 2)
        layout.addWidget(self.parallel_amf_check, 4, 3)
        layout.addWidget(self.parallel_cpu_check, 4, 4)
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

        self._set_status_snapshot("-", "-", "-", "-", None)
        self._restore_header_state()

    def _apply_translations(self) -> None:
        self.tr = get_translator(self.language, self.config_dir)
        self.setWindowTitle(self.tr.t("app.title"))
        self.source_box.setTitle(self.tr.t("gui.group.source"))
        self.jobs_box.setTitle(self.tr.t("gui.group.jobs"))

        self.add_files_action.setText(self.tr.t("gui.button.add_files"))
        self.add_folder_action.setText(self.tr.t("gui.button.add_folder"))
        self.plan_action.setText(self.tr.t("gui.button.add_to_queue"))
        self.start_queue_action.setText(self.tr.t("gui.button.start_queue"))
        self.pause_after_current_action.setText(self.tr.t("gui.button.pause_after_current"))
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
        self.parallel_check.setText(self.tr.t("gui.checkbox.parallel_enabled"))
        self.parallel_backends_label.setText(self.tr.t("gui.label.parallel_backends"))
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

        self.total_items_title.setText(self.tr.t("gui.summary.total_items"))
        self.states_title.setText(self.tr.t("gui.summary.states"))
        self.total_duration_title.setText(self.tr.t("gui.summary.total_duration"))
        self.saved_space_title.setText(self.tr.t("gui.summary.estimated_saved"))

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

        self.queue_model.set_translator(self.tr)
        self.activity_log_window.apply_translations(self.tr)
        self.queue_window.apply_translations(self.tr)
        self._set_status_snapshot(
            self._status_stage,
            self._status_file,
            self._status_speed,
            self._status_elapsed,
            self._status_percent,
        )
        self._update_queue_metrics(self.queue_model.metrics())

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

    def _set_status_snapshot(
        self,
        stage: str,
        file_name: str,
        speed: str,
        elapsed: str,
        percent: float | None,
    ) -> None:
        self._status_stage = stage
        self._status_file = file_name
        self._status_speed = speed
        self._status_elapsed = elapsed
        self._status_percent = percent
        self.status_stage_label.setText(self.tr.t("gui.statusbar.stage", value=stage))
        self.status_file_label.setText(self.tr.t("gui.statusbar.file", value=file_name))
        self.status_speed_label.setText(self.tr.t("gui.statusbar.speed", value=speed))
        self.status_elapsed_label.setText(self.tr.t("gui.statusbar.elapsed", value=elapsed))
        if percent is None:
            self.status_current_progress_label.setText(self.tr.t("gui.statusbar.current_progress", value="-"))
            self.current_progress_bar.setValue(0)
        else:
            bounded = max(0.0, min(100.0, percent))
            self.status_current_progress_label.setText(self.tr.t("gui.statusbar.current_progress", value=f"{bounded:.1f}%"))
            self.current_progress_bar.setValue(int(round(bounded * 10)))

    def _update_queue_metrics(self, metrics) -> None:
        self.total_items_value.setText(str(metrics.total_items))
        self.states_value.setText(
            self.tr.t(
                "gui.summary.queue_states",
                ready=metrics.ready_items,
                running=metrics.running_items,
                failed=metrics.failed_items,
            )
        )
        self.total_duration_value.setText(format_duration(metrics.total_duration_sec))
        self.saved_space_value.setText(
            format_size(metrics.estimated_saved_bytes) if metrics.estimated_saved_bytes is not None else self.tr.t("gui.value.unknown")
        )
        eta_text = format_duration(metrics.eta_sec) if metrics.eta_sec else self.tr.t("gui.value.unknown")
        self.queue_progress_text.setText(
            self.tr.t(
                "gui.summary.queue_progress",
                percent=f"{metrics.queue_percent:.1f}",
                completed=metrics.completed_items,
                total=metrics.total_items,
                eta=eta_text,
            )
        )
        self.queue_progress_bar.setValue(int(round(metrics.queue_percent * 10)))
        self.queue_window.update_metrics(metrics)
        self._refresh_action_state()

    def _language_changed(self, language: str) -> None:
        self.language = language
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
                options = self._current_options()
                self._validate_parallel_options_for_gui(options)
                path = save_preset(name, options, self.config_dir)
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
            parallel_enabled=self.parallel_check.isChecked(),
            parallel_backends=tuple(self._selected_parallel_backends()),
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
        self.parallel_check.setChecked(options.parallel_enabled)
        selected = set(options.parallel_backends)
        self.parallel_nvenc_check.setChecked(BackendChoice.NVENC in selected)
        self.parallel_qsv_check.setChecked(BackendChoice.QSV in selected)
        self.parallel_amf_check.setChecked(BackendChoice.AMF in selected)
        self.parallel_cpu_check.setChecked(BackendChoice.CPU in selected)
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
        parallel_enabled = self.parallel_check.isChecked()
        self.backend_combo.setEnabled(not parallel_enabled)
        for widget in [
            self.parallel_nvenc_check,
            self.parallel_qsv_check,
            self.parallel_amf_check,
            self.parallel_cpu_check,
        ]:
            widget.setEnabled(parallel_enabled)

    def _selected_parallel_backends(self) -> list[BackendChoice]:
        selected: list[BackendChoice] = []
        mapping = [
            (self.parallel_nvenc_check, BackendChoice.NVENC),
            (self.parallel_qsv_check, BackendChoice.QSV),
            (self.parallel_amf_check, BackendChoice.AMF),
            (self.parallel_cpu_check, BackendChoice.CPU),
        ]
        for checkbox, backend in mapping:
            if checkbox.isChecked():
                selected.append(backend)
        return selected

    def _validate_parallel_options_for_gui(self, options: EncodeOptions, *, allow_parallel: bool = True) -> None:
        if not options.parallel_enabled:
            return
        if not allow_parallel:
            raise ValueError(self.tr.t("gui.message.parallel_preview_not_supported"))
        if not options.parallel_backends:
            raise ValueError(self.tr.t("gui.message.parallel_requires_backends"))
        if options.two_pass:
            raise ValueError(self.tr.t("gui.message.parallel_two_pass_not_supported"))
        if options.encoder_preset:
            raise ValueError(self.tr.t("gui.message.parallel_preset_not_supported"))

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

    def _set_controls_enabled(self, enabled: bool) -> None:
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
            widget.setEnabled(enabled)

    def _refresh_action_state(self) -> None:
        plan_preview_busy = self.active_worker is not None
        queue_busy = self.queue_busy
        any_busy = plan_preview_busy or queue_busy
        has_queued_items = bool(self.queue_model.execution_records())

        self.add_files_action.setEnabled(not any_busy)
        self.add_folder_action.setEnabled(not any_busy)
        self.plan_action.setEnabled(not any_busy)
        self.start_queue_action.setEnabled(not any_busy and has_queued_items)
        self.pause_after_current_action.setEnabled(queue_busy)
        self.preview_action.setEnabled(not any_busy)
        self.stop_action.setEnabled(any_busy)
        self.presets_action.setEnabled(not any_busy)
        self.settings_action.setEnabled(not any_busy)
        self._set_controls_enabled(not any_busy)
        drag_drop_mode = QAbstractItemView.NoDragDrop if queue_busy else QAbstractItemView.InternalMove
        for view in [self.table_view, self.queue_window.table_view]:
            view.setDragDropMode(drag_drop_mode)
            view.setDragEnabled(not queue_busy)
            view.setAcceptDrops(not queue_busy)

    def _set_worker_busy(self, busy: bool) -> None:
        if not busy:
            self.active_worker = None
        self._refresh_action_state()

    def _start_worker(self, worker, completed_slot) -> None:
        self.active_worker = worker
        self._refresh_action_state()
        if hasattr(worker, "log"):
            worker.log.connect(self._append_log)
        if hasattr(worker, "progress"):
            worker.progress.connect(self._update_progress)
        worker.finished.connect(lambda: self._set_worker_busy(False))
        worker.failed.connect(self._on_worker_failed)
        if hasattr(worker, "cancelled"):
            worker.cancelled.connect(self._on_worker_cancelled)
        worker.completed.connect(completed_slot)
        worker.start()

    def _on_worker_failed(self, message: str) -> None:
        self._append_log(f"{self.tr.t('gui.message.error')}: {message}")
        self._set_status_snapshot(self.tr.t("gui.status.failed"), "-", "-", "-", None)
        QMessageBox.critical(self, self.tr.t("gui.message.error"), message)

    def _on_worker_cancelled(self, message: str) -> None:
        self._append_log(message)
        self._set_status_snapshot(self.tr.t("gui.status.cancelled"), "-", "-", "-", None)

    def _on_queue_busy_changed(self, busy: bool) -> None:
        self.queue_busy = busy
        self._refresh_action_state()

    def _on_queue_state_changed(self, state: str) -> None:
        self._queue_state = state
        if state == "pause_after_current":
            self._append_log(self.tr.t("gui.log.pause_after_current_requested"))
        elif state == "paused":
            self._append_log(self.tr.t("gui.log.queue_paused"))
        elif state == "cancelled":
            self._append_log(self.tr.t("gui.log.queue_cancelled"))
        elif state == "idle":
            self._append_log(self.tr.t("gui.log.encode_done"))

    def _on_queue_error(self, message: str) -> None:
        self._append_log(f"{self.tr.t('gui.message.error')}: {message}")
        QMessageBox.critical(self, self.tr.t("gui.message.error"), message)

    def _stop_active_task(self) -> None:
        if self.active_worker is not None and hasattr(self.active_worker, "cancel"):
            self._append_log(self.tr.t("gui.log.stop_requested"))
            self.active_worker.cancel()
            return
        if self.queue_busy:
            self._append_log(self.tr.t("gui.log.stop_requested"))
            self.queue_manager.stop()

    def _update_progress(self, event: dict[str, object]) -> None:
        stage = str(event.get("stage") or event.get("phase") or "-")
        state = str(event.get("state") or "-")
        file_name = str(event.get("file_name") or event.get("file_path") or "-")
        percent = event.get("file_progress")
        if not isinstance(percent, (int, float)):
            percent = event.get("percent")
        speed = str(event.get("speed") or "-")
        elapsed_sec = event.get("elapsed_sec")
        duration_sec = event.get("duration_sec")

        if isinstance(elapsed_sec, (int, float)):
            elapsed_text = format_duration(float(elapsed_sec))
            if isinstance(duration_sec, (int, float)) and float(duration_sec) > 0:
                elapsed_text = f"{elapsed_text} / {format_duration(float(duration_sec))}"
        elif isinstance(duration_sec, (int, float)) and float(duration_sec) > 0:
            elapsed_text = format_duration(float(duration_sec))
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
        self._validate_parallel_options_for_gui(options)
        output_dir = self._selected_output()
        workdir = self._selected_workdir()
        ffmpeg_path = self._selected_ffmpeg()
        ffprobe_path = self._selected_ffprobe()
        self._persist_runtime_state()
        return input_path, options, output_dir, workdir, ffmpeg_path, ffprobe_path

    def _plan_current_source(self) -> None:
        try:
            input_path, options, output_dir, workdir, ffmpeg_path, ffprobe_path = self._build_context()
        except Exception as exc:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), str(exc))
            return

        self._append_log(self.tr.t("gui.log.planning"))
        self._set_status_snapshot("planning", "-", "-", "-", 0.0)
        worker = PlanWorker(
            input_path=input_path,
            options=options,
            output_dir=output_dir,
            workdir=workdir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
        self._start_worker(worker, lambda plan, workdir=workdir: self._on_plan_ready(plan, workdir))

    def _start_plan_for_files(self, files: list[Path]) -> None:
        if not files:
            return
        options = self._current_options()
        try:
            self._validate_parallel_options_for_gui(options)
        except Exception as exc:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), str(exc))
            return
        output_dir = self._selected_output()
        workdir = self._selected_workdir()
        ffmpeg_path = self._selected_ffmpeg()
        ffprobe_path = self._selected_ffprobe()
        self._persist_runtime_state()
        file_items = [VideoFileItem(path=path.resolve(), relative_path=Path(path.name)) for path in files]
        self._append_log(self.tr.t("gui.log.planning"))
        self._set_status_snapshot("planning", "-", "-", "-", 0.0)
        worker = PlanWorker(
            input_path=None,
            options=options,
            output_dir=output_dir,
            workdir=workdir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            files=file_items,
        )
        self._start_worker(worker, lambda plan, workdir=workdir: self._on_plan_ready(plan, workdir))

    def _add_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr.t("gui.dialog.select_source_file"))
        if not paths:
            return
        self.source_combo.setEditText(paths[0])
        self._persist_runtime_state()
        self._start_plan_for_files([Path(path) for path in paths])

    def _add_folder_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_source_dir"))
        if not path:
            return
        self.source_combo.setEditText(path)
        self._persist_runtime_state()
        self._plan_current_source()

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
        try:
            self._validate_parallel_options_for_gui(options, allow_parallel=False)
        except Exception as exc:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), str(exc))
            return
        self._append_log(self.tr.t("gui.log.previewing"))
        self._set_status_snapshot("preview", input_path.name, "-", "-", 0.0)
        worker = PreviewWorker(
            input_path=input_path,
            options=options,
            preview_options=preview_options,
            output_dir=output_dir,
            workdir=workdir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
        self._start_worker(worker, self._on_preview_ready)

    def _start_queue(self) -> None:
        if not self.queue_manager.start():
            QMessageBox.information(self, self.tr.t("gui.message.info"), self.tr.t("gui.message.no_queued_items"))
            return
        self._append_log(self.tr.t("gui.log.encoding"))
        self._set_status_snapshot("queue / starting", "-", "-", "-", 0.0)

    def _pause_after_current(self) -> None:
        if self.queue_manager.pause_after_current():
            return
        QMessageBox.information(self, self.tr.t("gui.message.info"), self.tr.t("gui.message.no_running_queue"))

    def _show_queue_window(self) -> None:
        self.queue_window.show()
        self.queue_window.raise_()
        self.queue_window.activateWindow()

    def _show_activity_log(self) -> None:
        self.activity_log_window.show()
        self.activity_log_window.raise_()
        self.activity_log_window.activateWindow()

    def _on_plan_ready(self, plan, workdir: Path) -> None:
        added = self.queue_manager.add_plan(plan, workdir)
        valid_items = [item for item in plan.items if not item.skip_reason]
        skipped_items = [item for item in plan.items if item.skip_reason]
        self._append_log(
            self.tr.t(
                "gui.log.items_added_to_queue",
                total=added,
                ready=len(valid_items),
                skipped=len(skipped_items),
            )
        )
        self._set_status_snapshot(self.tr.t("gui.status.done"), "-", "-", "-", 100.0)

    def _on_preview_ready(self, result) -> None:
        if result.success:
            summary = [
                self.tr.t("gui.summary.preview_source", path=result.job.source_path),
                self.tr.t("gui.summary.preview_window", start=result.job.start_sec, duration=result.job.duration_sec),
                self.tr.t("gui.summary.preview_source_sample", path=result.job.source_sample_path),
                self.tr.t("gui.summary.preview_encoded_sample", path=result.job.encoded_sample_path),
                self.tr.t("gui.summary.preview_ratio", value=f"{result.sample_compression_ratio:.3f}"),
                self.tr.t("gui.summary.preview_estimated_size", value=format_size(result.estimated_full_output_size)),
                self.tr.t("gui.summary.log_path", path=result.log_path or ""),
            ]
            self._append_log(self.tr.t("gui.log.preview_done"))
            self._append_log(
                self.tr.t(
                    "gui.log.preview_ratio",
                    ratio=f"{result.sample_compression_ratio:.3f}",
                    size=format_size(result.estimated_full_output_size),
                )
            )
            dialog = PreviewResultDialog(self.tr, summary, self)
            dialog.exec()
            self._set_status_snapshot(self.tr.t("gui.status.done"), result.job.source_path.name, "-", "-", 100.0)
            return

        self._append_log(f"{self.tr.t('gui.message.error')}: {result.error_message}")
        QMessageBox.critical(self, self.tr.t("gui.message.error"), result.error_message or "Preview failed.")

    def _selected_rows_from_view(self, view) -> list[int]:
        return sorted(index.row() for index in view.selectionModel().selectedRows())

    def _show_queue_context_menu(self, view, pos: QPoint) -> None:
        menu = QMenu(self)
        rows = self._selected_rows_from_view(view)
        selected_record = self.queue_model.record_for_row(rows[0]) if rows else None

        open_source_action = menu.addAction(self.tr.t("gui.menu.open_source_folder"))
        open_output_action = menu.addAction(self.tr.t("gui.menu.open_output_folder"))
        copy_source_action = menu.addAction(self.tr.t("gui.menu.copy_source_path"))
        copy_output_action = menu.addAction(self.tr.t("gui.menu.copy_output_path"))
        menu.addSeparator()
        retry_action = menu.addAction(self.tr.t("gui.menu.retry_selected"))
        remove_action = menu.addAction(self.tr.t("gui.menu.remove_from_queue"))
        clear_completed_action = menu.addAction(self.tr.t("gui.menu.clear_completed"))

        has_selection = bool(rows)
        open_source_action.setEnabled(has_selection)
        open_output_action.setEnabled(has_selection)
        copy_source_action.setEnabled(has_selection)
        copy_output_action.setEnabled(has_selection)
        retry_action.setEnabled(has_selection and self.queue_model.can_retry_rows(rows) and not self.queue_busy)
        remove_action.setEnabled(has_selection and self.queue_model.can_remove_rows(rows) and not self.queue_busy)
        clear_completed_action.setEnabled(not self.queue_busy)

        action = menu.exec(view.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == open_source_action and selected_record is not None:
            self._open_folder(selected_record.source_path.parent)
        elif action == open_output_action and selected_record is not None:
            self._open_folder(selected_record.output_path.parent)
        elif action == copy_source_action and selected_record is not None:
            self._copy_to_clipboard(str(selected_record.source_path))
        elif action == copy_output_action and selected_record is not None:
            self._copy_to_clipboard(str(selected_record.output_path))
        elif action == retry_action:
            retried = self.queue_manager.retry_rows(rows)
            if retried:
                self._append_log(self.tr.t("gui.log.retry_selected", count=retried))
        elif action == remove_action:
            removed = self.queue_manager.remove_rows(rows)
            if removed:
                self._append_log(self.tr.t("gui.log.removed_from_queue", count=removed))
        elif action == clear_completed_action:
            removed = self.queue_manager.clear_completed()
            if removed:
                self._append_log(self.tr.t("gui.log.cleared_completed", count=removed))

    def _show_header_context_menu(self, view, pos: QPoint) -> None:
        menu = QMenu(self)
        header = view.horizontalHeader()
        for column in range(self.queue_model.columnCount()):
            label = self.queue_model.headerData(column, Qt.Horizontal, Qt.DisplayRole)
            action = menu.addAction(str(label))
            action.setCheckable(True)
            action.setChecked(not view.isColumnHidden(column))
            action.setData(column)
        chosen = menu.exec(header.mapToGlobal(pos))
        if chosen is None:
            return
        column = int(chosen.data())
        view.setColumnHidden(column, not chosen.isChecked())
        self._persist_header_state(view)
        self._reflow_queue_views()

    def _reflow_queue_views(self) -> None:
        for view in [self.table_view, self.queue_window.table_view]:
            reflow = getattr(view, "schedule_reflow", None)
            if callable(reflow):
                reflow()

    def _persist_header_state(self, source_view) -> None:
        if self._header_sync_guard:
            return
        state = source_view.horizontalHeader().saveState()
        self.app_config["queue_table_header_state"] = bytes(state.toBase64()).decode("ascii")
        save_app_config(self.config_dir, self.app_config)

        self._header_sync_guard = True
        try:
            for view in [self.table_view, self.queue_window.table_view]:
                if view is source_view:
                    continue
                view.horizontalHeader().restoreState(state)
            self._reflow_queue_views()
        finally:
            self._header_sync_guard = False

    def _restore_header_state(self) -> None:
        encoded = str(self.app_config.get("queue_table_header_state", "")).strip()
        if not encoded:
            self._reflow_queue_views()
            return
        try:
            raw = QByteArray.fromBase64(encoded.encode("ascii"))
        except Exception:
            self._reflow_queue_views()
            return
        self._header_sync_guard = True
        try:
            for view in [self.table_view, self.queue_window.table_view]:
                view.horizontalHeader().restoreState(raw)
            self._reflow_queue_views()
        finally:
            self._header_sync_guard = False

    def _copy_to_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _open_folder(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
