from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from core.i18n import Translator


class SettingsDialog(QDialog):
    def __init__(self, tr: Translator, settings: dict[str, object], parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self._build_ui()
        self._load_settings(settings)
        self.apply_translations(tr)

    def _build_ui(self) -> None:
        self.resize(720, 280)
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("简体中文", "zh_cn")

        self.workdir_label = QLabel()
        self.workdir_edit = QLineEdit()
        self.workdir_button = QPushButton()

        self.ffmpeg_label = QLabel()
        self.ffmpeg_edit = QLineEdit()
        self.ffmpeg_button = QPushButton()

        self.ffprobe_label = QLabel()
        self.ffprobe_edit = QLineEdit()
        self.ffprobe_button = QPushButton()

        self.log_level_label = QLabel()
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["info", "debug"])

        self.keep_preview_temp_check = QCheckBox()

        layout.addWidget(self.language_label, 0, 0)
        layout.addWidget(self.language_combo, 0, 1, 1, 2)

        layout.addWidget(self.workdir_label, 1, 0)
        layout.addWidget(self.workdir_edit, 1, 1)
        layout.addWidget(self.workdir_button, 1, 2)

        layout.addWidget(self.ffmpeg_label, 2, 0)
        layout.addWidget(self.ffmpeg_edit, 2, 1)
        layout.addWidget(self.ffmpeg_button, 2, 2)

        layout.addWidget(self.ffprobe_label, 3, 0)
        layout.addWidget(self.ffprobe_edit, 3, 1)
        layout.addWidget(self.ffprobe_button, 3, 2)

        layout.addWidget(self.log_level_label, 4, 0)
        layout.addWidget(self.log_level_combo, 4, 1, 1, 2)

        layout.addWidget(self.keep_preview_temp_check, 5, 0, 1, 3)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(self.button_box, 6, 0, 1, 3)

        self.workdir_button.clicked.connect(self._browse_workdir)
        self.ffmpeg_button.clicked.connect(self._browse_ffmpeg)
        self.ffprobe_button.clicked.connect(self._browse_ffprobe)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

    def _load_settings(self, settings: dict[str, object]) -> None:
        language = str(settings.get("language", "en"))
        index = self.language_combo.findData(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        self.workdir_edit.setText(str(settings.get("workdir_path", "")))
        self.ffmpeg_edit.setText(str(settings.get("ffmpeg_path", "")))
        self.ffprobe_edit.setText(str(settings.get("ffprobe_path", "")))
        self.log_level_combo.setCurrentText(str(settings.get("log_level", "info")))
        self.keep_preview_temp_check.setChecked(bool(settings.get("keep_preview_temp", True)))

    def apply_translations(self, tr: Translator) -> None:
        self.tr = tr
        self.setWindowTitle(self.tr.t("gui.window.settings"))
        self.language_label.setText(self.tr.t("gui.label.language"))
        self.workdir_label.setText(self.tr.t("gui.label.workdir"))
        self.ffmpeg_label.setText(self.tr.t("gui.label.ffmpeg"))
        self.ffprobe_label.setText(self.tr.t("gui.label.ffprobe"))
        self.log_level_label.setText(self.tr.t("gui.label.log_level"))
        self.keep_preview_temp_check.setText(self.tr.t("gui.checkbox.keep_preview_temp"))
        self.workdir_button.setText(self.tr.t("gui.button.browse_dir"))
        self.ffmpeg_button.setText(self.tr.t("gui.button.browse_exe"))
        self.ffprobe_button.setText(self.tr.t("gui.button.browse_exe"))
        self.ffmpeg_edit.setPlaceholderText(self.tr.t("gui.placeholder.ffmpeg"))
        self.ffprobe_edit.setPlaceholderText(self.tr.t("gui.placeholder.ffprobe"))
        self.ffmpeg_edit.setToolTip(self.tr.t("gui.placeholder.ffmpeg"))
        self.ffprobe_edit.setToolTip(self.tr.t("gui.placeholder.ffprobe"))

    def values(self) -> dict[str, object]:
        return {
            "language": self.language_combo.currentData(),
            "workdir_path": self.workdir_edit.text().strip(),
            "ffmpeg_path": self.ffmpeg_edit.text().strip(),
            "ffprobe_path": self.ffprobe_edit.text().strip(),
            "log_level": self.log_level_combo.currentText(),
            "keep_preview_temp": self.keep_preview_temp_check.isChecked(),
        }

    def _browse_workdir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr.t("gui.dialog.select_workdir"))
        if path:
            self.workdir_edit.setText(path)

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr.t("gui.dialog.select_ffmpeg"))
        if path:
            self.ffmpeg_edit.setText(path)

    def _browse_ffprobe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr.t("gui.dialog.select_ffprobe"))
        if path:
            self.ffprobe_edit.setText(path)
