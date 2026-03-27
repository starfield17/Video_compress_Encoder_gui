from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from core.i18n import Translator


class PreviewResultDialog(QDialog):
    def __init__(self, tr: Translator, lines: list[str], parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self._build_ui(lines)
        self.apply_translations(tr)

    def _build_ui(self, lines: list[str]) -> None:
        self.resize(760, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlainText("\n".join(lines))

        self.button_box = QDialogButtonBox(QDialogButtonBox.Close)
        self.button_box.rejected.connect(self.reject)
        self.button_box.accepted.connect(self.accept)

        layout.addWidget(self.output, 1)
        layout.addWidget(self.button_box)

    def apply_translations(self, tr: Translator) -> None:
        self.tr = tr
        self.setWindowTitle(self.tr.t("gui.window.preview_result"))
