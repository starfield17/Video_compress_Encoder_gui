from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView, QStyle, QTableView

from core.bitrate_policy import human_kbps
from core.i18n import Translator
from gui.queue_state import (
    QueueItemRecord,
    QueueItemStatus,
    QueueMetrics,
    build_tags,
    build_tooltip,
    compute_metrics,
    mark_cancelled,
    mark_failed,
    mark_finished,
    mark_started,
    reset_for_retry,
    short_error,
    status_key,
)


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "n/a"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "n/a"
    negative = size_bytes < 0
    value = float(abs(size_bytes))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            formatted = f"{value:.2f} {unit}"
            return "-" + formatted if negative else formatted
        value /= 1024.0
    return str(size_bytes)


class QueueColumn(IntEnum):
    NAME = 0
    FOLDER = 1
    RESOLUTION = 2
    DURATION = 3
    SOURCE_BITRATE = 4
    TARGET_BITRATE = 5
    ENCODER = 6
    OUTPUT = 7
    TAGS = 8
    STATUS = 9
    PROGRESS = 10


COLUMN_COUNT = len(QueueColumn)


class QueueTableModel(QAbstractTableModel):
    metricsChanged = Signal(object)

    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self._records: list[QueueItemRecord] = []
        self._metrics = QueueMetrics()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return COLUMN_COUNT

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Vertical:
            return section + 1
        labels = {
            QueueColumn.NAME: self.tr.t("gui.table.name"),
            QueueColumn.FOLDER: self.tr.t("gui.table.folder"),
            QueueColumn.RESOLUTION: self.tr.t("gui.table.resolution"),
            QueueColumn.DURATION: self.tr.t("gui.table.duration"),
            QueueColumn.SOURCE_BITRATE: self.tr.t("gui.table.source_bitrate"),
            QueueColumn.TARGET_BITRATE: self.tr.t("gui.table.target_bitrate"),
            QueueColumn.ENCODER: self.tr.t("gui.table.encoder"),
            QueueColumn.OUTPUT: self.tr.t("gui.table.output"),
            QueueColumn.TAGS: self.tr.t("gui.table.tags"),
            QueueColumn.STATUS: self.tr.t("gui.table.status"),
            QueueColumn.PROGRESS: self.tr.t("gui.table.progress"),
        }
        return labels.get(QueueColumn(section), "")

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self._records[index.row()]
        column = QueueColumn(index.column())
        media = record.media_info

        if role == Qt.DisplayRole:
            if column == QueueColumn.NAME:
                return record.source_path.name
            if column == QueueColumn.FOLDER:
                return str(record.source_path.parent)
            if column == QueueColumn.RESOLUTION:
                if media and media.width and media.height:
                    return f"{media.width}x{media.height}"
                return "n/a"
            if column == QueueColumn.DURATION:
                return format_duration(media.duration if media else None)
            if column == QueueColumn.SOURCE_BITRATE:
                return human_kbps(media.video_bitrate_bps) if media else "n/a"
            if column == QueueColumn.TARGET_BITRATE:
                return human_kbps(record.plan_item.target_video_bitrate_bps) if record.plan_item.target_video_bitrate_bps else "n/a"
            if column == QueueColumn.ENCODER:
                encoder = record.plan_item.encoder_info
                return f"{encoder.encoder_name} ({encoder.backend.value})" if encoder else "n/a"
            if column == QueueColumn.OUTPUT:
                return record.output_path.name
            if column == QueueColumn.TAGS:
                return " ".join(build_tags(record))
            if column == QueueColumn.STATUS:
                return self.tr.t(status_key(record.status))
            if column == QueueColumn.PROGRESS:
                if record.status in {QueueItemStatus.QUEUED, QueueItemStatus.DRAFT}:
                    return "-"
                return f"{max(0.0, min(100.0, record.file_progress)):.1f}%"
        elif role == Qt.ToolTipRole:
            if column == QueueColumn.FOLDER:
                return str(record.source_path.parent)
            if column == QueueColumn.OUTPUT:
                return str(record.output_path)
            if column == QueueColumn.TAGS and record.error_summary:
                return build_tooltip(record)
            return build_tooltip(record)
        elif role == Qt.TextAlignmentRole:
            if column in {
                QueueColumn.RESOLUTION,
                QueueColumn.DURATION,
                QueueColumn.SOURCE_BITRATE,
                QueueColumn.TARGET_BITRATE,
                QueueColumn.STATUS,
                QueueColumn.PROGRESS,
            }:
                return int(Qt.AlignCenter)
        elif role == Qt.ForegroundRole and column in {QueueColumn.STATUS, QueueColumn.PROGRESS}:
            palette = {
                QueueItemStatus.RUNNING: QColor("#0B5394"),
                QueueItemStatus.DONE: QColor("#38761D"),
                QueueItemStatus.FAILED: QColor("#A61C00"),
                QueueItemStatus.CANCELLED: QColor("#7F6000"),
                QueueItemStatus.SKIPPED: QColor("#666666"),
                QueueItemStatus.PAUSED: QColor("#7F6000"),
            }
            return palette.get(record.status)
        elif role == Qt.DecorationRole and column == QueueColumn.STATUS:
            style = QApplication.style()
            if style is None:
                return None
            if record.status == QueueItemStatus.RUNNING:
                return style.standardIcon(QStyle.SP_MediaPlay)
            if record.status == QueueItemStatus.DONE:
                return style.standardIcon(QStyle.SP_DialogApplyButton)
            if record.status == QueueItemStatus.FAILED:
                return style.standardIcon(QStyle.SP_MessageBoxCritical)
            if record.status == QueueItemStatus.CANCELLED:
                return style.standardIcon(QStyle.SP_DialogCancelButton)
            if record.status == QueueItemStatus.SKIPPED:
                return style.standardIcon(QStyle.SP_MessageBoxWarning)
            if record.status == QueueItemStatus.PAUSED:
                return style.standardIcon(QStyle.SP_MediaPause)
        elif role == Qt.UserRole:
            return record.item_id
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        default_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if not index.isValid():
            return default_flags | Qt.ItemIsDropEnabled
        record = self._records[index.row()]
        if record.status != QueueItemStatus.RUNNING:
            default_flags |= Qt.ItemIsDragEnabled
        return default_flags | Qt.ItemIsDropEnabled

    def supportedDropActions(self) -> Qt.DropActions:
        return Qt.MoveAction

    def moveRows(
        self,
        source_parent: QModelIndex,
        source_row: int,
        count: int,
        destination_parent: QModelIndex,
        destination_child: int,
    ) -> bool:
        if count <= 0:
            return False
        if source_parent.isValid() or destination_parent.isValid():
            return False
        if source_row < 0 or source_row + count > len(self._records):
            return False
        if destination_child < 0 or destination_child > len(self._records):
            return False
        if destination_child >= source_row and destination_child <= source_row + count:
            return False
        moving = self._records[source_row : source_row + count]
        if any(record.status == QueueItemStatus.RUNNING for record in moving):
            return False

        self.beginMoveRows(source_parent, source_row, source_row + count - 1, destination_parent, destination_child)
        del self._records[source_row : source_row + count]
        if destination_child > source_row:
            destination_child -= count
        for offset, record in enumerate(moving):
            self._records.insert(destination_child + offset, record)
        self.endMoveRows()
        self._emit_metrics_changed()
        return True

    def set_translator(self, tr: Translator) -> None:
        self.tr = tr
        if self.rowCount() > 0:
            top_left = self.index(0, 0)
            bottom_right = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right)
        self.headerDataChanged.emit(Qt.Horizontal, 0, self.columnCount() - 1)
        self._emit_metrics_changed()

    def records(self) -> list[QueueItemRecord]:
        return self._records

    def metrics(self) -> QueueMetrics:
        return self._metrics

    def record_for_row(self, row: int) -> QueueItemRecord | None:
        if row < 0 or row >= len(self._records):
            return None
        return self._records[row]

    def record_for_id(self, item_id: str) -> tuple[int, QueueItemRecord] | tuple[None, None]:
        for row, record in enumerate(self._records):
            if record.item_id == item_id:
                return row, record
        return None, None

    def add_records(self, records: list[QueueItemRecord]) -> None:
        if not records:
            return
        start = len(self._records)
        end = start + len(records) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._records.extend(records)
        self.endInsertRows()
        self._emit_metrics_changed()

    def remove_rows_by_index(self, rows: list[int]) -> int:
        targets = sorted({row for row in rows if 0 <= row < len(self._records)}, reverse=True)
        removed = 0
        for row in targets:
            if self._records[row].status == QueueItemStatus.RUNNING:
                continue
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._records[row]
            self.endRemoveRows()
            removed += 1
        if removed:
            self._emit_metrics_changed()
        return removed

    def clear_completed(self) -> int:
        targets = [
            row
            for row, record in enumerate(self._records)
            if record.status in {QueueItemStatus.DONE, QueueItemStatus.SKIPPED, QueueItemStatus.CANCELLED}
        ]
        return self.remove_rows_by_index(targets)

    def retry_rows(self, rows: list[int]) -> int:
        retried = 0
        changed_rows: list[int] = []
        for row in sorted(set(rows)):
            record = self.record_for_row(row)
            if record is None:
                continue
            if record.status not in {QueueItemStatus.FAILED, QueueItemStatus.CANCELLED}:
                continue
            reset_for_retry(record)
            retried += 1
            changed_rows.append(row)
        self._emit_rows_changed(changed_rows)
        return retried

    def prepare_for_execution(self, item_ids: list[str]) -> None:
        changed_rows: list[int] = []
        for item_id in item_ids:
            row, record = self.record_for_id(item_id)
            if row is None or record is None:
                continue
            if record.status == QueueItemStatus.QUEUED:
                record.last_speed = ""
                record.elapsed_sec = None
                changed_rows.append(row)
        self._emit_rows_changed(changed_rows)

    def execution_records(self) -> list[QueueItemRecord]:
        return [record for record in self._records if record.status == QueueItemStatus.QUEUED]

    def mark_running(self, item_id: str) -> None:
        row, record = self.record_for_id(item_id)
        if row is None or record is None:
            return
        mark_started(record)
        self._emit_rows_changed([row])

    def mark_cancelled(self, item_id: str, message: str | None = None) -> None:
        row, record = self.record_for_id(item_id)
        if row is None or record is None:
            return
        mark_cancelled(record, message)
        self._emit_rows_changed([row])

    def mark_failed(self, item_id: str, message: str | None = None) -> None:
        row, record = self.record_for_id(item_id)
        if row is None or record is None:
            return
        mark_failed(record, message)
        self._emit_rows_changed([row])

    def apply_progress_event(self, event: dict[str, object]) -> None:
        item_id = str(event.get("queue_item_id") or "")
        if not item_id:
            return
        row, record = self.record_for_id(item_id)
        if row is None or record is None:
            return
        state = str(event.get("state") or "")
        if state in {"starting_file", "running_pass"} and record.status != QueueItemStatus.RUNNING:
            record.status = QueueItemStatus.RUNNING
        current_pass_index = event.get("current_pass_index")
        if isinstance(current_pass_index, int):
            record.current_pass_index = current_pass_index
        total_passes = event.get("total_passes")
        if isinstance(total_passes, int) and total_passes > 0:
            record.total_passes = total_passes
        pass_percent = event.get("pass_percent")
        if isinstance(pass_percent, (int, float)):
            record.pass_percent = max(0.0, min(100.0, float(pass_percent)))
        file_progress = event.get("file_progress")
        if isinstance(file_progress, (int, float)):
            record.file_progress = max(0.0, min(100.0, float(file_progress)))
        percent = event.get("percent")
        if isinstance(percent, (int, float)) and state not in {"finished_file", "failed_file"}:
            record.file_progress = max(0.0, min(100.0, float(percent)))
        speed = event.get("speed")
        if isinstance(speed, str) and speed:
            record.last_speed = speed
        elapsed_sec = event.get("elapsed_sec")
        if isinstance(elapsed_sec, (int, float)):
            record.elapsed_sec = float(elapsed_sec)
        message = short_error(str(event.get("message") or "").strip())
        if message and state in {"failed_file", "cancelled_file"}:
            record.error_summary = message
        self._emit_rows_changed([row])

    def apply_result(self, item_id: str, result) -> None:
        row, record = self.record_for_id(item_id)
        if row is None or record is None:
            return
        mark_finished(record, result)
        self._emit_rows_changed([row])

    def can_remove_rows(self, rows: list[int]) -> bool:
        for row in rows:
            record = self.record_for_row(row)
            if record is not None and record.status == QueueItemStatus.RUNNING:
                return False
        return True

    def can_retry_rows(self, rows: list[int]) -> bool:
        return any(
            self.record_for_row(row) is not None
            and self.record_for_row(row).status in {QueueItemStatus.FAILED, QueueItemStatus.CANCELLED}
            for row in rows
        )

    def _emit_rows_changed(self, rows: list[int]) -> None:
        clean_rows = sorted({row for row in rows if 0 <= row < len(self._records)})
        if clean_rows:
            for row in clean_rows:
                self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))
        self._emit_metrics_changed()

    def _emit_metrics_changed(self) -> None:
        self._metrics = compute_metrics(self._records)
        self.metricsChanged.emit(self._metrics)


def create_queue_view(parent=None) -> QTableView:
    view = QTableView(parent)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    view.setAlternatingRowColors(True)
    view.setSortingEnabled(False)
    view.setWordWrap(False)
    view.setTextElideMode(Qt.ElideMiddle)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    view.setDragEnabled(True)
    view.setAcceptDrops(True)
    view.setDropIndicatorShown(True)
    view.setDragDropMode(QAbstractItemView.InternalMove)
    view.setDefaultDropAction(Qt.MoveAction)
    header = view.horizontalHeader()
    header.setStretchLastSection(False)
    header.setSectionsMovable(True)
    header.setSectionsClickable(True)
    header.setHighlightSections(False)
    header.setSectionResizeMode(QueueColumn.NAME, QHeaderView.Interactive)
    header.setSectionResizeMode(QueueColumn.FOLDER, QHeaderView.Interactive)
    header.setSectionResizeMode(QueueColumn.RESOLUTION, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(QueueColumn.DURATION, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(QueueColumn.SOURCE_BITRATE, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(QueueColumn.TARGET_BITRATE, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(QueueColumn.ENCODER, QHeaderView.Interactive)
    header.setSectionResizeMode(QueueColumn.OUTPUT, QHeaderView.Interactive)
    header.setSectionResizeMode(QueueColumn.TAGS, QHeaderView.Interactive)
    header.setSectionResizeMode(QueueColumn.STATUS, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(QueueColumn.PROGRESS, QHeaderView.ResizeToContents)
    default_widths = {
        QueueColumn.NAME: 260,
        QueueColumn.FOLDER: 240,
        QueueColumn.ENCODER: 170,
        QueueColumn.OUTPUT: 220,
        QueueColumn.TAGS: 160,
    }
    for column, width in default_widths.items():
        view.setColumnWidth(int(column), width)
    view.verticalHeader().setVisible(False)
    return view
