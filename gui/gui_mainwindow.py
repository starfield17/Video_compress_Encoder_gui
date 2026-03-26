from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
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
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
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
from core.preset_store import (
    delete_preset,
    list_presets,
    load_app_config,
    load_preset,
    save_app_config,
    save_preset,
)
from gui.gui_workers import EncodeWorker, PlanWorker, PreviewWorker


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

        self._build_ui()
        self._load_initial_state()
        self._apply_translations()
        self._sync_dependent_controls()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        runtime_box = QGroupBox()
        runtime_layout = QGridLayout(runtime_box)

        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("简体中文", "zh_cn")

        self.source_label = QLabel()
        self.source_edit = QLineEdit()
        self.source_file_button = QPushButton()
        self.source_dir_button = QPushButton()

        self.output_label = QLabel()
        self.output_edit = QLineEdit()
        self.output_button = QPushButton()

        self.workdir_label = QLabel()
        self.workdir_edit = QLineEdit()
        self.workdir_button = QPushButton()

        self.ffmpeg_label = QLabel()
        self.ffmpeg_edit = QLineEdit()
        self.ffmpeg_button = QPushButton()

        self.ffprobe_label = QLabel()
        self.ffprobe_edit = QLineEdit()
        self.ffprobe_button = QPushButton()

        runtime_layout.addWidget(self.language_label, 0, 0)
        runtime_layout.addWidget(self.language_combo, 0, 1)

        runtime_layout.addWidget(self.source_label, 1, 0)
        runtime_layout.addWidget(self.source_edit, 1, 1)
        runtime_layout.addWidget(self.source_file_button, 1, 2)
        runtime_layout.addWidget(self.source_dir_button, 1, 3)

        runtime_layout.addWidget(self.output_label, 2, 0)
        runtime_layout.addWidget(self.output_edit, 2, 1, 1, 2)
        runtime_layout.addWidget(self.output_button, 2, 3)

        runtime_layout.addWidget(self.workdir_label, 3, 0)
        runtime_layout.addWidget(self.workdir_edit, 3, 1, 1, 2)
        runtime_layout.addWidget(self.workdir_button, 3, 3)

        runtime_layout.addWidget(self.ffmpeg_label, 4, 0)
        runtime_layout.addWidget(self.ffmpeg_edit, 4, 1, 1, 2)
        runtime_layout.addWidget(self.ffmpeg_button, 4, 3)

        runtime_layout.addWidget(self.ffprobe_label, 5, 0)
        runtime_layout.addWidget(self.ffprobe_edit, 5, 1, 1, 2)
        runtime_layout.addWidget(self.ffprobe_button, 5, 3)

        encode_box = QGroupBox()
        encode_layout = QGridLayout(encode_box)

        self.preset_label = QLabel()
        self.preset_combo = QComboBox()
        self.refresh_presets_button = QPushButton()
        self.load_preset_button = QPushButton()
        self.save_preset_button = QPushButton()
        self.delete_preset_button = QPushButton()

        self.codec_label = QLabel()
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["hevc", "av1"])

        self.backend_label = QLabel()
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["auto", "cpu", "nvenc", "amf"])

        self.ratio_label = QLabel()
        self.ratio_edit = QLineEdit()

        self.container_label = QLabel()
        self.container_combo = QComboBox()
        self.container_combo.addItems(["mkv", "mp4"])

        self.audio_mode_label = QLabel()
        self.audio_mode_combo = QComboBox()
        self.audio_mode_combo.addItems(["copy", "aac"])

        self.audio_bitrate_label = QLabel()
        self.audio_bitrate_edit = QLineEdit()

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

        self.recursive_check = QCheckBox()
        self.overwrite_check = QCheckBox()
        self.copy_subtitles_check = QCheckBox()
        self.two_pass_check = QCheckBox()

        preset_buttons = QHBoxLayout()
        preset_buttons.addWidget(self.refresh_presets_button)
        preset_buttons.addWidget(self.load_preset_button)
        preset_buttons.addWidget(self.save_preset_button)
        preset_buttons.addWidget(self.delete_preset_button)

        row = 0
        encode_layout.addWidget(self.preset_label, row, 0)
        encode_layout.addWidget(self.preset_combo, row, 1, 1, 2)
        encode_layout.addLayout(preset_buttons, row, 3, 1, 2)
        row += 1

        encode_layout.addWidget(self.codec_label, row, 0)
        encode_layout.addWidget(self.codec_combo, row, 1)
        encode_layout.addWidget(self.backend_label, row, 2)
        encode_layout.addWidget(self.backend_combo, row, 3)
        encode_layout.addWidget(self.container_label, row, 4)
        encode_layout.addWidget(self.container_combo, row, 5)
        row += 1

        encode_layout.addWidget(self.ratio_label, row, 0)
        encode_layout.addWidget(self.ratio_edit, row, 1)
        encode_layout.addWidget(self.audio_mode_label, row, 2)
        encode_layout.addWidget(self.audio_mode_combo, row, 3)
        encode_layout.addWidget(self.audio_bitrate_label, row, 4)
        encode_layout.addWidget(self.audio_bitrate_edit, row, 5)
        row += 1

        encode_layout.addWidget(self.encoder_preset_label, row, 0)
        encode_layout.addWidget(self.encoder_preset_edit, row, 1)
        encode_layout.addWidget(self.pix_fmt_label, row, 2)
        encode_layout.addWidget(self.pix_fmt_edit, row, 3)
        encode_layout.addWidget(self.min_bitrate_label, row, 4)
        encode_layout.addWidget(self.min_bitrate_spin, row, 5)
        row += 1

        encode_layout.addWidget(self.max_bitrate_label, row, 0)
        encode_layout.addWidget(self.max_bitrate_spin, row, 1)
        encode_layout.addWidget(self.maxrate_factor_label, row, 2)
        encode_layout.addWidget(self.maxrate_factor_spin, row, 3)
        encode_layout.addWidget(self.bufsize_factor_label, row, 4)
        encode_layout.addWidget(self.bufsize_factor_spin, row, 5)
        row += 1

        encode_layout.addWidget(self.sample_mode_label, row, 0)
        encode_layout.addWidget(self.sample_mode_combo, row, 1)
        encode_layout.addWidget(self.sample_duration_label, row, 2)
        encode_layout.addWidget(self.sample_duration_spin, row, 3)
        encode_layout.addWidget(self.sample_start_label, row, 4)
        encode_layout.addWidget(self.sample_start_spin, row, 5)
        row += 1

        flags = QHBoxLayout()
        flags.addWidget(self.recursive_check)
        flags.addWidget(self.overwrite_check)
        flags.addWidget(self.copy_subtitles_check)
        flags.addWidget(self.two_pass_check)
        encode_layout.addLayout(flags, row, 0, 1, 6)

        action_box = QGroupBox()
        action_layout = QHBoxLayout(action_box)
        self.plan_button = QPushButton()
        self.preview_button = QPushButton()
        self.encode_button = QPushButton()
        self.clear_log_button = QPushButton()
        action_layout.addWidget(self.plan_button)
        action_layout.addWidget(self.preview_button)
        action_layout.addWidget(self.encode_button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.clear_log_button)

        summary_box = QGroupBox()
        summary_layout = QVBoxLayout(summary_box)
        self.summary_output = QTextEdit()
        self.summary_output.setReadOnly(True)
        self.summary_output.setMinimumHeight(120)
        summary_layout.addWidget(self.summary_output)

        self.table = QTableWidget(0, 9)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        bottom_splitter = QSplitter(Qt.Vertical)
        bottom_splitter.addWidget(summary_box)
        bottom_splitter.addWidget(self.table)
        bottom_splitter.addWidget(self.log_output)
        bottom_splitter.setStretchFactor(0, 1)
        bottom_splitter.setStretchFactor(1, 3)
        bottom_splitter.setStretchFactor(2, 2)

        root_layout.addWidget(runtime_box)
        root_layout.addWidget(encode_box)
        root_layout.addWidget(action_box)
        root_layout.addWidget(bottom_splitter, 1)

        self.runtime_box = runtime_box
        self.encode_box = encode_box
        self.action_box = action_box
        self.summary_box = summary_box

        self.source_file_button.clicked.connect(self._browse_source_file)
        self.source_dir_button.clicked.connect(self._browse_source_dir)
        self.output_button.clicked.connect(self._browse_output)
        self.workdir_button.clicked.connect(self._browse_workdir)
        self.ffmpeg_button.clicked.connect(self._browse_ffmpeg)
        self.ffprobe_button.clicked.connect(self._browse_ffprobe)
        self.refresh_presets_button.clicked.connect(self._refresh_presets)
        self.load_preset_button.clicked.connect(self._load_selected_preset)
        self.save_preset_button.clicked.connect(self._save_preset_dialog)
        self.delete_preset_button.clicked.connect(self._delete_selected_preset)
        self.plan_button.clicked.connect(self._plan)
        self.preview_button.clicked.connect(self._preview)
        self.encode_button.clicked.connect(self._encode)
        self.clear_log_button.clicked.connect(self.log_output.clear)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        self.sample_mode_combo.currentIndexChanged.connect(self._sync_dependent_controls)
        self.audio_mode_combo.currentIndexChanged.connect(self._sync_dependent_controls)

    def _load_initial_state(self) -> None:
        language_index = self.language_combo.findData(self.language)
        if language_index >= 0:
            self.language_combo.setCurrentIndex(language_index)

        self.workdir_edit.setText(self.app_config.get("workdir_path", str(self.default_workdir)))
        self.ffmpeg_edit.setText(self.app_config.get("ffmpeg_path", ""))
        self.ffprobe_edit.setText(self.app_config.get("ffprobe_path", ""))
        self.source_edit.setText(self.app_config.get("last_source_path", ""))
        self.output_edit.setText(self.app_config.get("last_output_dir", ""))

        self._refresh_presets()
        default_preset = self.app_config.get("default_preset_name")
        if default_preset:
            preset_index = self.preset_combo.findText(default_preset)
            if preset_index >= 0:
                self.preset_combo.setCurrentIndex(preset_index)
                self._load_selected_preset()
        else:
            self._apply_options(EncodeOptions())

    def _apply_translations(self) -> None:
        self.setWindowTitle(self.tr.t("app.title"))
        self.runtime_box.setTitle(self.tr.t("gui.group.runtime"))
        self.encode_box.setTitle(self.tr.t("gui.group.encode"))
        self.action_box.setTitle(self.tr.t("gui.group.actions"))
        self.summary_box.setTitle(self.tr.t("gui.group.summary"))

        self.language_label.setText(self.tr.t("gui.label.language"))
        self.source_label.setText(self.tr.t("gui.label.source"))
        self.output_label.setText(self.tr.t("gui.label.output"))
        self.workdir_label.setText(self.tr.t("gui.label.workdir"))
        self.ffmpeg_label.setText(self.tr.t("gui.label.ffmpeg"))
        self.ffprobe_label.setText(self.tr.t("gui.label.ffprobe"))
        self.preset_label.setText(self.tr.t("gui.label.preset"))
        self.codec_label.setText(self.tr.t("gui.label.codec"))
        self.backend_label.setText(self.tr.t("gui.label.backend"))
        self.ratio_label.setText(self.tr.t("gui.label.ratio"))
        self.container_label.setText(self.tr.t("gui.label.container"))
        self.audio_mode_label.setText(self.tr.t("gui.label.audio_mode"))
        self.audio_bitrate_label.setText(self.tr.t("gui.label.audio_bitrate"))
        self.encoder_preset_label.setText(self.tr.t("gui.label.encoder_preset"))
        self.pix_fmt_label.setText(self.tr.t("gui.label.pix_fmt"))
        self.min_bitrate_label.setText(self.tr.t("gui.label.min_video_kbps"))
        self.max_bitrate_label.setText(self.tr.t("gui.label.max_video_kbps"))
        self.maxrate_factor_label.setText(self.tr.t("gui.label.maxrate_factor"))
        self.bufsize_factor_label.setText(self.tr.t("gui.label.bufsize_factor"))
        self.sample_mode_label.setText(self.tr.t("gui.label.sample_mode"))
        self.sample_duration_label.setText(self.tr.t("gui.label.sample_duration"))
        self.sample_start_label.setText(self.tr.t("gui.label.sample_start"))

        self.source_file_button.setText(self.tr.t("gui.button.browse_file"))
        self.source_dir_button.setText(self.tr.t("gui.button.browse_dir"))
        self.output_button.setText(self.tr.t("gui.button.browse_dir"))
        self.workdir_button.setText(self.tr.t("gui.button.browse_dir"))
        self.ffmpeg_button.setText(self.tr.t("gui.button.browse_exe"))
        self.ffprobe_button.setText(self.tr.t("gui.button.browse_exe"))
        self.refresh_presets_button.setText(self.tr.t("gui.button.refresh_presets"))
        self.load_preset_button.setText(self.tr.t("gui.button.load_preset"))
        self.save_preset_button.setText(self.tr.t("gui.button.save_preset"))
        self.delete_preset_button.setText(self.tr.t("gui.button.delete_preset"))
        self.plan_button.setText(self.tr.t("gui.button.plan"))
        self.preview_button.setText(self.tr.t("gui.button.preview"))
        self.encode_button.setText(self.tr.t("gui.button.encode"))
        self.clear_log_button.setText(self.tr.t("gui.button.clear_log"))

        self.recursive_check.setText(self.tr.t("gui.checkbox.recursive"))
        self.overwrite_check.setText(self.tr.t("gui.checkbox.overwrite"))
        self.copy_subtitles_check.setText(self.tr.t("gui.checkbox.copy_subtitles"))
        self.two_pass_check.setText(self.tr.t("gui.checkbox.two_pass"))

        self.source_edit.setPlaceholderText(self.tr.t("gui.placeholder.source"))
        self.output_edit.setPlaceholderText(self.tr.t("gui.placeholder.default_output"))
        self.workdir_edit.setPlaceholderText(str(self.default_workdir))
        self.ffmpeg_edit.setPlaceholderText(self.tr.t("gui.placeholder.ffmpeg"))
        self.ffprobe_edit.setPlaceholderText(self.tr.t("gui.placeholder.ffprobe"))
        self.ratio_edit.setPlaceholderText(self.tr.t("gui.placeholder.auto_ratio"))
        self.encoder_preset_edit.setPlaceholderText(self.tr.t("gui.placeholder.encoder_preset"))
        self.pix_fmt_edit.setPlaceholderText("yuv420p")

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

    def _append_log(self, message: str) -> None:
        self.log_output.append(message)

    def _set_summary(self, lines: list[str]) -> None:
        self.summary_output.setPlainText("\n".join(lines))

    def _language_changed(self) -> None:
        self.language = self.language_combo.currentData()
        self.tr = get_translator(self.language, self.config_dir)
        self.app_config["language"] = self.language
        self._persist_runtime_state()
        self._apply_translations()
        self._sync_dependent_controls()

    def _browse_source_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr.t("gui.dialog.select_source_file"))
        if path:
            self.source_edit.setText(path)
            self._persist_runtime_state()

    def _browse_source_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_source_dir"))
        if path:
            self.source_edit.setText(path)
            self._persist_runtime_state()

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_output_dir"))
        if path:
            self.output_edit.setText(path)
            self._persist_runtime_state()

    def _browse_workdir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_workdir"))
        if path:
            self.workdir_edit.setText(path)
            self._persist_runtime_state()

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr.t("gui.dialog.select_ffmpeg"))
        if path:
            self.ffmpeg_edit.setText(path)
            self._persist_runtime_state()

    def _browse_ffprobe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr.t("gui.dialog.select_ffprobe"))
        if path:
            self.ffprobe_edit.setText(path)
            self._persist_runtime_state()

    def _refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItem("")
        self.preset_combo.addItems(list_presets(self.config_dir))
        index = self.preset_combo.findText(current)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)

    def _load_selected_preset(self) -> None:
        name = self.preset_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), self.tr.t("gui.message.no_preset_selected"))
            return
        options = load_preset(name, self.config_dir)
        self._apply_options(options)
        self.app_config["default_preset_name"] = name
        self._persist_runtime_state()
        self._append_log(self.tr.t("gui.log.preset_loaded", name=name))

    def _save_preset_dialog(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            self.tr.t("gui.button.save_preset"),
            self.tr.t("gui.message.enter_preset_name"),
        )
        if not ok or not name.strip():
            return
        path = save_preset(name.strip(), self._current_options(), self.config_dir)
        self.app_config["default_preset_name"] = name.strip()
        self._refresh_presets()
        preset_index = self.preset_combo.findText(name.strip())
        if preset_index >= 0:
            self.preset_combo.setCurrentIndex(preset_index)
        self._persist_runtime_state()
        self._append_log(self.tr.t("gui.log.preset_saved", name=name.strip(), path=path))

    def _delete_selected_preset(self) -> None:
        name = self.preset_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), self.tr.t("gui.message.no_preset_selected"))
            return
        delete_preset(name, self.config_dir)
        if self.app_config.get("default_preset_name") == name:
            self.app_config["default_preset_name"] = ""
        self._refresh_presets()
        self._persist_runtime_state()
        self._append_log(self.tr.t("gui.log.preset_deleted", name=name))

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
        self.two_pass_check.setChecked(options.two_pass)
        self.overwrite_check.setChecked(options.overwrite)
        self.recursive_check.setChecked(options.recursive)
        self._sync_dependent_controls()

    def _selected_input(self) -> Path | None:
        text = self.source_edit.text().strip()
        return Path(text).expanduser().resolve() if text else None

    def _selected_output(self) -> Path | None:
        text = self.output_edit.text().strip()
        return Path(text).expanduser().resolve() if text else None

    def _selected_workdir(self) -> Path:
        text = self.workdir_edit.text().strip()
        return Path(text).expanduser().resolve() if text else self.default_workdir.resolve()

    def _selected_ffmpeg(self) -> str | None:
        text = self.ffmpeg_edit.text().strip()
        return text or None

    def _selected_ffprobe(self) -> str | None:
        text = self.ffprobe_edit.text().strip()
        return text or None

    def _sync_dependent_controls(self) -> None:
        custom_sample = self.sample_mode_combo.currentText() == PreviewSampleMode.CUSTOM.value
        self.sample_start_spin.setEnabled(custom_sample)
        self.audio_bitrate_edit.setEnabled(self.audio_mode_combo.currentText() == AudioMode.AAC.value)

    def _persist_runtime_state(self) -> None:
        source_text = self.source_edit.text().strip()
        output_text = self.output_edit.text().strip()
        self.app_config["language"] = self.language
        self.app_config["last_source_path"] = source_text
        self.app_config["last_output_dir"] = output_text
        self.app_config["workdir_path"] = self.workdir_edit.text().strip() or str(self.default_workdir)
        self.app_config["ffmpeg_path"] = self.ffmpeg_edit.text().strip()
        self.app_config["ffprobe_path"] = self.ffprobe_edit.text().strip()

        recent_paths = list(self.app_config.get("recent_paths", []))
        if source_text:
            recent_paths = [item for item in recent_paths if item != source_text]
            recent_paths.insert(0, source_text)
            self.app_config["recent_paths"] = recent_paths[:10]
        save_app_config(self.config_dir, self.app_config)

    def _set_busy(self, busy: bool) -> None:
        for widget in [
            self.plan_button,
            self.preview_button,
            self.encode_button,
            self.refresh_presets_button,
            self.load_preset_button,
            self.save_preset_button,
            self.delete_preset_button,
            self.clear_log_button,
            self.source_file_button,
            self.source_dir_button,
            self.output_button,
            self.workdir_button,
            self.ffmpeg_button,
            self.ffprobe_button,
        ]:
            widget.setEnabled(not busy)

    def _start_worker(self, worker) -> None:
        self.active_worker = worker
        self._set_busy(True)
        worker.finished.connect(lambda: self._set_busy(False))
        worker.finished.connect(lambda: setattr(self, "active_worker", None))
        worker.failed.connect(self._on_worker_failed)
        worker.start()

    def _on_worker_failed(self, message: str) -> None:
        self._append_log(f"{self.tr.t('gui.message.error')}: {message}")
        QMessageBox.critical(self, self.tr.t("gui.message.error"), message)

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
            custom_start_sec=float(self.sample_start_spin.value()) if self.sample_mode_combo.currentText() == PreviewSampleMode.CUSTOM.value else None,
        )
        self._append_log(self.tr.t("gui.log.previewing"))
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

    def _on_plan_ready(self, plan) -> None:
        self._populate_table(plan)
        valid_items = [item for item in plan.items if not item.skip_reason]
        skipped_items = [item for item in plan.items if item.skip_reason]
        summary = [
            self.tr.t("gui.summary.mode", mode=self.tr.t("gui.button.plan")),
            self.tr.t("gui.summary.plan_count", total=len(plan.items), ready=len(valid_items), skipped=len(skipped_items)),
            self.tr.t("gui.summary.output_root", path=plan.output_root),
            self.tr.t("gui.summary.ffmpeg", path=plan.ffmpeg_path),
            self.tr.t("gui.summary.ffprobe", path=plan.ffprobe_path),
        ]
        self._set_summary(summary)
        self._append_log(self.tr.t("gui.log.plan_ready"))

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
            QMessageBox.information(
                self,
                self.tr.t("gui.message.info"),
                "\n".join(summary[2:7]),
            )
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
            self.tr.t("gui.summary.mode", mode=self.tr.t("gui.button.encode")),
            self.tr.t("gui.summary.encode_count", success=success_count, skipped=skipped_count, failed=failed_count),
            self.tr.t("gui.summary.output_root", path=plan.output_root),
            self.tr.t("gui.summary.ffmpeg", path=plan.ffmpeg_path),
            self.tr.t("gui.summary.ffprobe", path=plan.ffprobe_path),
        ]
        self._set_summary(summary)
        self._append_log(self.tr.t("gui.log.encode_done"))

    def _populate_table(self, plan, results=None) -> None:
        result_map = {str(result.source_path): result for result in results or []}
        self.table.setRowCount(len(plan.items))
        for row, item in enumerate(plan.items):
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
            values = [
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
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, col, cell)
