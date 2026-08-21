from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from core.i18n import Translator
from core.models import PreviewResult, SmartPreviewResult


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return str(size_bytes)


def build_preview_summary(
    tr: Translator,
    result: PreviewResult | SmartPreviewResult,
) -> list[str]:
    """Build the translated preview report without depending on a window."""
    if isinstance(result, SmartPreviewResult):
        quality = result.quality_search_result
        lines = [
            tr.t("gui.summary.preview_source", path=result.source_path),
            tr.t(
                "gui.summary.smart_target_bitrate",
                value=(
                    f"{quality.selected_video_bitrate_bps / 1000:.0f} kbps"
                    if quality.selected_video_bitrate_bps
                    else "-"
                ),
            ),
            tr.t(
                "gui.summary.smart_min_vmaf",
                value=f"{quality.min_vmaf:.2f}" if quality.min_vmaf is not None else "-",
            ),
            tr.t(
                "gui.summary.smart_predicted_ratio",
                value=(
                    f"{quality.predicted_output_ratio * 100:.2f}%"
                    if quality.predicted_output_ratio is not None
                    else "-"
                ),
            ),
            tr.t(
                "gui.summary.smart_required_ratio",
                value=(
                    f"{quality.required_output_ratio * 100:.2f}%"
                    if quality.required_output_ratio is not None
                    else "-"
                ),
            ),
            tr.t("gui.summary.log_path", path=result.log_path or ""),
        ]
        if result.error_message:
            lines.append(f"{tr.t('gui.message.warning')}: {result.error_message}")
        return lines

    return [
        tr.t("gui.summary.preview_source", path=result.job.source_path),
        tr.t(
            "gui.summary.preview_window",
            start=result.job.start_sec,
            duration=result.job.duration_sec,
        ),
        tr.t("gui.summary.preview_source_sample", path=result.job.source_sample_path),
        tr.t("gui.summary.preview_encoded_sample", path=result.job.encoded_sample_path),
        tr.t("gui.summary.preview_ratio", value=f"{result.sample_compression_ratio:.3f}"),
        tr.t(
            "gui.summary.preview_estimated_size",
            value=_format_size(result.estimated_full_output_size),
        ),
        tr.t("gui.summary.log_path", path=result.log_path or ""),
    ]


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
