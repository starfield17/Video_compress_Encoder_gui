from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.i18n import Translator


class QueueWindow(QMainWindow):
    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self._build_ui()
        self.apply_translations(tr)

    def _build_ui(self) -> None:
        self.resize(1180, 680)
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)

        self.table = QTableWidget(0, 9)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout.addWidget(self.summary_label)
        layout.addWidget(self.table, 1)

    def apply_translations(self, tr: Translator) -> None:
        self.tr = tr
        self.setWindowTitle(self.tr.t("gui.window.queue"))
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

    def set_summary_lines(self, lines: list[str]) -> None:
        self.summary_label.setText("\n".join(lines))

    def set_rows(self, rows: list[list[str]]) -> None:
        self.table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for col_index, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row_index, col_index, cell)
