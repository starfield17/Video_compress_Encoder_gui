from __future__ import annotations

from pathlib import Path

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QFileDialog,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import (
        QFileDialog,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

from core.bitrate_policy import human_kbps
from core.i18n import get_translator
from core.models import AudioMode, BackendChoice, CodecChoice, ContainerChoice, EncodeOptions, PreviewOptions, PreviewSampleMode
from core.preset_store import load_app_config, load_preset, save_app_config, save_preset, list_presets
from gui.gui_workers import EncodeWorker, PlanWorker, PreviewWorker


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

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        grid = QGridLayout()
        layout.addLayout(grid)

        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("简体中文", "zh_cn")

        self.source_label = QLabel()
        self.source_edit = QLineEdit()
        self.browse_source_button = QPushButton()

        self.output_label = QLabel()
        self.output_edit = QLineEdit()
        self.browse_output_button = QPushButton()

        self.preset_label = QLabel()
        self.preset_combo = QComboBox()
        self.refresh_presets_button = QPushButton()
        self.load_preset_button = QPushButton()
        self.save_preset_button = QPushButton()

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

        self.min_bitrate_label = QLabel()
        self.min_bitrate_spin = QSpinBox()
        self.min_bitrate_spin.setRange(0, 500000)
        self.min_bitrate_spin.setValue(250)

        self.max_bitrate_label = QLabel()
        self.max_bitrate_spin = QSpinBox()
        self.max_bitrate_spin.setRange(0, 500000)
        self.max_bitrate_spin.setValue(0)

        self.sample_mode_label = QLabel()
        self.sample_mode_combo = QComboBox()
        self.sample_mode_combo.addItems(["middle", "custom"])

        self.sample_duration_label = QLabel()
        self.sample_duration_spin = QDoubleSpinBox()
        self.sample_duration_spin.setRange(1.0, 3600.0)
        self.sample_duration_spin.setValue(30.0)

        self.sample_start_label = QLabel()
        self.sample_start_spin = QDoubleSpinBox()
        self.sample_start_spin.setRange(0.0, 86400.0)
        self.sample_start_spin.setValue(0.0)

        self.recursive_check = QCheckBox()
        self.overwrite_check = QCheckBox()
        self.copy_subtitles_check = QCheckBox()
        self.two_pass_check = QCheckBox()

        self.plan_button = QPushButton()
        self.preview_button = QPushButton()
        self.encode_button = QPushButton()

        row = 0
        grid.addWidget(self.language_label, row, 0)
        grid.addWidget(self.language_combo, row, 1)
        row += 1

        grid.addWidget(self.source_label, row, 0)
        grid.addWidget(self.source_edit, row, 1)
        grid.addWidget(self.browse_source_button, row, 2)
        row += 1

        grid.addWidget(self.output_label, row, 0)
        grid.addWidget(self.output_edit, row, 1)
        grid.addWidget(self.browse_output_button, row, 2)
        row += 1

        preset_buttons = QHBoxLayout()
        preset_buttons.addWidget(self.refresh_presets_button)
        preset_buttons.addWidget(self.load_preset_button)
        preset_buttons.addWidget(self.save_preset_button)
        grid.addWidget(self.preset_label, row, 0)
        grid.addWidget(self.preset_combo, row, 1)
        grid.addLayout(preset_buttons, row, 2)
        row += 1

        grid.addWidget(self.codec_label, row, 0)
        grid.addWidget(self.codec_combo, row, 1)
        grid.addWidget(self.backend_label, row, 2)
        grid.addWidget(self.backend_combo, row, 3)
        row += 1

        grid.addWidget(self.ratio_label, row, 0)
        grid.addWidget(self.ratio_edit, row, 1)
        grid.addWidget(self.container_label, row, 2)
        grid.addWidget(self.container_combo, row, 3)
        row += 1

        grid.addWidget(self.audio_mode_label, row, 0)
        grid.addWidget(self.audio_mode_combo, row, 1)
        grid.addWidget(self.audio_bitrate_label, row, 2)
        grid.addWidget(self.audio_bitrate_edit, row, 3)
        row += 1

        grid.addWidget(self.encoder_preset_label, row, 0)
        grid.addWidget(self.encoder_preset_edit, row, 1)
        grid.addWidget(self.min_bitrate_label, row, 2)
        grid.addWidget(self.min_bitrate_spin, row, 3)
        row += 1

        grid.addWidget(self.max_bitrate_label, row, 0)
        grid.addWidget(self.max_bitrate_spin, row, 1)
        grid.addWidget(self.sample_mode_label, row, 2)
        grid.addWidget(self.sample_mode_combo, row, 3)
        row += 1

        grid.addWidget(self.sample_duration_label, row, 0)
        grid.addWidget(self.sample_duration_spin, row, 1)
        grid.addWidget(self.sample_start_label, row, 2)
        grid.addWidget(self.sample_start_spin, row, 3)
        row += 1

        flags = QHBoxLayout()
        flags.addWidget(self.recursive_check)
        flags.addWidget(self.overwrite_check)
        flags.addWidget(self.copy_subtitles_check)
        flags.addWidget(self.two_pass_check)
        layout.addLayout(flags)

        actions = QHBoxLayout()
        actions.addWidget(self.plan_button)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.encode_button)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 6)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)

        self.browse_source_button.clicked.connect(self._browse_source)
        self.browse_output_button.clicked.connect(self._browse_output)
        self.refresh_presets_button.clicked.connect(self._refresh_presets)
        self.load_preset_button.clicked.connect(self._load_selected_preset)
        self.save_preset_button.clicked.connect(self._save_preset_dialog)
        self.plan_button.clicked.connect(self._plan)
        self.preview_button.clicked.connect(self._preview)
        self.encode_button.clicked.connect(self._encode)
        self.language_combo.currentIndexChanged.connect(self._language_changed)

    def _load_initial_state(self) -> None:
        index = self.language_combo.findData(self.language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        self._refresh_presets()
        default_preset = self.app_config.get("default_preset_name")
        if default_preset:
            preset_index = self.preset_combo.findText(default_preset)
            if preset_index >= 0:
                self.preset_combo.setCurrentIndex(preset_index)
                self._load_selected_preset()

    def _apply_translations(self) -> None:
        self.setWindowTitle(self.tr.t("app.title"))
        self.language_label.setText(self.tr.t("gui.label.language"))
        self.source_label.setText(self.tr.t("gui.label.source"))
        self.output_label.setText(self.tr.t("gui.label.output"))
        self.preset_label.setText(self.tr.t("gui.label.preset"))
        self.codec_label.setText(self.tr.t("gui.label.codec"))
        self.backend_label.setText(self.tr.t("gui.label.backend"))
        self.ratio_label.setText(self.tr.t("gui.label.ratio"))
        self.container_label.setText(self.tr.t("gui.label.container"))
        self.audio_mode_label.setText(self.tr.t("gui.label.audio_mode"))
        self.audio_bitrate_label.setText(self.tr.t("gui.label.audio_bitrate"))
        self.encoder_preset_label.setText(self.tr.t("gui.label.encoder_preset"))
        self.min_bitrate_label.setText(self.tr.t("gui.label.min_video_kbps"))
        self.max_bitrate_label.setText(self.tr.t("gui.label.max_video_kbps"))
        self.sample_mode_label.setText(self.tr.t("gui.label.sample_mode"))
        self.sample_duration_label.setText(self.tr.t("gui.label.sample_duration"))
        self.sample_start_label.setText(self.tr.t("gui.label.sample_start"))

        self.browse_source_button.setText(self.tr.t("gui.button.browse"))
        self.browse_output_button.setText(self.tr.t("gui.button.browse"))
        self.refresh_presets_button.setText(self.tr.t("gui.button.refresh_presets"))
        self.load_preset_button.setText(self.tr.t("gui.button.load_preset"))
        self.save_preset_button.setText(self.tr.t("gui.button.save_preset"))
        self.plan_button.setText(self.tr.t("gui.button.plan"))
        self.preview_button.setText(self.tr.t("gui.button.preview"))
        self.encode_button.setText(self.tr.t("gui.button.encode"))

        self.recursive_check.setText(self.tr.t("gui.checkbox.recursive"))
        self.overwrite_check.setText(self.tr.t("gui.checkbox.overwrite"))
        self.copy_subtitles_check.setText(self.tr.t("gui.checkbox.copy_subtitles"))
        self.two_pass_check.setText(self.tr.t("gui.checkbox.two_pass"))

        self.ratio_edit.setPlaceholderText(self.tr.t("gui.placeholder.auto_ratio"))
        self.output_edit.setPlaceholderText(self.tr.t("gui.placeholder.default_output"))
        self.encoder_preset_edit.setPlaceholderText(self.tr.t("gui.placeholder.encoder_preset"))

        self.table.setHorizontalHeaderLabels(
            [
                self.tr.t("gui.table.source"),
                self.tr.t("gui.table.source_bitrate"),
                self.tr.t("gui.table.target_bitrate"),
                self.tr.t("gui.table.encoder"),
                self.tr.t("gui.table.output"),
                self.tr.t("gui.table.status"),
            ]
        )

    def _append_log(self, message: str) -> None:
        self.log_output.append(message)

    def _language_changed(self) -> None:
        self.language = self.language_combo.currentData()
        self.tr = get_translator(self.language, self.config_dir)
        self.app_config["language"] = self.language
        save_app_config(self.config_dir, self.app_config)
        self._apply_translations()

    def _browse_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr.t("gui.dialog.select_source_file"))
        if not path:
            path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_source_dir"))
        if path:
            self.source_edit.setText(path)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_output_dir"))
        if path:
            self.output_edit.setText(path)

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
        self._append_log(self.tr.t("gui.log.preset_loaded", name=name))

    def _save_preset_dialog(self) -> None:
        name, ok = QInputDialog.getText(self, self.tr.t("gui.button.save_preset"), self.tr.t("gui.message.enter_preset_name"))
        if not ok or not name.strip():
            return
        path = save_preset(name.strip(), self._current_options(), self.config_dir)
        self._refresh_presets()
        self._append_log(self.tr.t("gui.log.preset_saved", name=name.strip(), path=path))

    def _current_options(self) -> EncodeOptions:
        ratio_text = self.ratio_edit.text().strip()
        encoder_preset = self.encoder_preset_edit.text().strip() or None
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
        self.min_bitrate_spin.setValue(options.min_video_kbps)
        self.max_bitrate_spin.setValue(options.max_video_kbps)
        self.copy_subtitles_check.setChecked(options.copy_subtitles)
        self.two_pass_check.setChecked(options.two_pass)
        self.overwrite_check.setChecked(options.overwrite)
        self.recursive_check.setChecked(options.recursive)

    def _selected_input(self) -> Path | None:
        text = self.source_edit.text().strip()
        return Path(text).expanduser().resolve() if text else None

    def _selected_output(self) -> Path | None:
        text = self.output_edit.text().strip()
        return Path(text).expanduser().resolve() if text else None

    def _set_busy(self, busy: bool) -> None:
        for widget in [
            self.plan_button,
            self.preview_button,
            self.encode_button,
            self.refresh_presets_button,
            self.load_preset_button,
            self.save_preset_button,
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

    def _plan(self) -> None:
        input_path = self._selected_input()
        if input_path is None:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), self.tr.t("gui.message.select_source"))
            return
        self._append_log(self.tr.t("gui.log.planning"))
        worker = PlanWorker(
            input_path=input_path,
            options=self._current_options(),
            output_dir=self._selected_output(),
            workdir=self.default_workdir,
            ffmpeg_path=None,
            ffprobe_path=None,
        )
        worker.completed.connect(self._on_plan_ready)
        self._start_worker(worker)

    def _preview(self) -> None:
        input_path = self._selected_input()
        if input_path is None:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), self.tr.t("gui.message.select_source"))
            return
        if not input_path.is_file():
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), self.tr.t("gui.message.preview_requires_file"))
            return
        preview_options = PreviewOptions(
            sample_mode=PreviewSampleMode(self.sample_mode_combo.currentText()),
            sample_duration_sec=float(self.sample_duration_spin.value()),
            custom_start_sec=float(self.sample_start_spin.value()) if self.sample_mode_combo.currentText() == "custom" else None,
        )
        self._append_log(self.tr.t("gui.log.previewing"))
        worker = PreviewWorker(
            input_path=input_path,
            options=self._current_options(),
            preview_options=preview_options,
            output_dir=self._selected_output(),
            workdir=self.default_workdir,
            ffmpeg_path=None,
            ffprobe_path=None,
        )
        worker.completed.connect(self._on_preview_ready)
        self._start_worker(worker)

    def _encode(self) -> None:
        input_path = self._selected_input()
        if input_path is None:
            QMessageBox.warning(self, self.tr.t("gui.message.warning"), self.tr.t("gui.message.select_source"))
            return
        self._append_log(self.tr.t("gui.log.encoding"))
        worker = EncodeWorker(
            input_path=input_path,
            options=self._current_options(),
            output_dir=self._selected_output(),
            workdir=self.default_workdir,
            ffmpeg_path=None,
            ffprobe_path=None,
        )
        worker.completed.connect(self._on_encode_ready)
        self._start_worker(worker)

    def _on_plan_ready(self, plan) -> None:
        self._populate_table(plan)
        self._append_log(self.tr.t("gui.log.plan_ready"))

    def _on_preview_ready(self, result) -> None:
        if result.success:
            self._append_log(self.tr.t("gui.log.preview_done"))
            self._append_log(
                f"{self.tr.t('cli.sample_ratio')}: {result.sample_compression_ratio:.3f}, "
                f"{self.tr.t('cli.estimated_output')}: {result.estimated_full_output_size}"
            )
            QMessageBox.information(
                self,
                self.tr.t("gui.message.info"),
                f"{self.tr.t('cli.sample_ratio')}: {result.sample_compression_ratio:.3f}\n"
                f"{self.tr.t('cli.estimated_output')}: {result.estimated_full_output_size}",
            )
        else:
            self._append_log(f"{self.tr.t('gui.message.error')}: {result.error_message}")
            QMessageBox.critical(self, self.tr.t("gui.message.error"), result.error_message or "Preview failed.")

    def _on_encode_ready(self, payload) -> None:
        plan, results = payload
        self._populate_table(plan, results)
        self._append_log(self.tr.t("gui.log.encode_done"))

    def _populate_table(self, plan, results=None) -> None:
        result_map = {str(result.source_path): result for result in results or []}
        self.table.setRowCount(len(plan.items))
        for row, item in enumerate(plan.items):
            media = item.media_info
            encoder = item.encoder_info
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

            values = [
                str(item.source_path),
                human_kbps(media.video_bitrate_bps) if media else "n/a",
                human_kbps(item.target_video_bitrate_bps) if item.target_video_bitrate_bps else "n/a",
                f"{encoder.encoder_name} ({encoder.backend.value})" if encoder else "n/a",
                str(item.output_path),
                status,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

