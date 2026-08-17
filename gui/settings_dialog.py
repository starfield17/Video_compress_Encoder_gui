from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.analysis_profiles import (
    FACTORY_ANALYSIS_PROFILES,
    all_analysis_profile_payloads,
    analysis_settings_payload,
    parse_analysis_profile_name,
    resolve_analysis_settings,
    validate_analysis_settings,
)
from core.i18n import Translator
from core.models import (
    AnalysisProfileName,
    AnalysisProfileSettings,
    QualityUnreachablePolicy,
    SizeBlockedPolicy,
    SkippedOutputPolicy,
)
from core.preset_store import smart_policies_from_config

VMAF_SUBSAMPLE_CHOICES = (1, 3, 5, 7)


class _AnalysisProfilePage(QWidget):
    def __init__(self, name: AnalysisProfileName, parent=None) -> None:
        super().__init__(parent)
        self.profile_name = name
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.whole_video_label = QLabel()
        self.whole_video_spin = QDoubleSpinBox()
        self.whole_video_spin.setRange(1.0, 60.0)
        self.whole_video_spin.setDecimals(1)
        self.whole_video_spin.setSingleStep(1.0)

        self.sample_duration_label = QLabel()
        self.sample_duration_spin = QDoubleSpinBox()
        self.sample_duration_spin.setRange(1.0, 30.0)
        self.sample_duration_spin.setDecimals(1)
        self.sample_duration_spin.setSingleStep(0.5)

        self.window_count_label = QLabel()
        self.window_count_spin = QSpinBox()
        self.window_count_spin.setRange(1, 5)

        self.coarse_candidates_label = QLabel()
        self.coarse_candidates_spin = QSpinBox()
        self.coarse_candidates_spin.setRange(1, 8)

        self.exact_candidates_label = QLabel()
        self.exact_candidates_spin = QSpinBox()
        self.exact_candidates_spin.setRange(2, 8)

        self.coarse_subsample_label = QLabel()
        self.coarse_subsample_combo = QComboBox()
        self.exact_subsample_label = QLabel()
        self.exact_subsample_combo = QComboBox()
        for value in VMAF_SUBSAMPLE_CHOICES:
            self.coarse_subsample_combo.addItem(str(value), value)
            self.exact_subsample_combo.addItem(str(value), value)

        self.min_tolerance_label = QLabel()
        self.min_tolerance_spin = QSpinBox()
        self.min_tolerance_spin.setRange(1, 500)
        self.min_tolerance_spin.setSingleStep(5)

        self.search_ratio_label = QLabel()
        self.search_ratio_spin = QDoubleSpinBox()
        self.search_ratio_spin.setRange(0.5, 25.0)
        self.search_ratio_spin.setDecimals(1)
        self.search_ratio_spin.setSingleStep(0.5)
        self.search_ratio_spin.setSuffix(" %")

        self.reset_button = QPushButton()
        self.reset_button.clicked.connect(self._reset_to_factory)

        layout.addWidget(self.whole_video_label, 0, 0)
        layout.addWidget(self.whole_video_spin, 0, 1)
        layout.addWidget(self.sample_duration_label, 0, 2)
        layout.addWidget(self.sample_duration_spin, 0, 3)
        layout.addWidget(self.window_count_label, 1, 0)
        layout.addWidget(self.window_count_spin, 1, 1)
        layout.addWidget(self.coarse_candidates_label, 1, 2)
        layout.addWidget(self.coarse_candidates_spin, 1, 3)
        layout.addWidget(self.exact_candidates_label, 2, 0)
        layout.addWidget(self.exact_candidates_spin, 2, 1)
        layout.addWidget(self.coarse_subsample_label, 2, 2)
        layout.addWidget(self.coarse_subsample_combo, 2, 3)
        layout.addWidget(self.exact_subsample_label, 3, 0)
        layout.addWidget(self.exact_subsample_combo, 3, 1)
        layout.addWidget(self.min_tolerance_label, 3, 2)
        layout.addWidget(self.min_tolerance_spin, 3, 3)
        layout.addWidget(self.search_ratio_label, 4, 0)
        layout.addWidget(self.search_ratio_spin, 4, 1)
        layout.addWidget(self.reset_button, 5, 0, 1, 4)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

    def set_settings(self, settings: AnalysisProfileSettings) -> None:
        resolved = validate_analysis_settings(settings)
        self.whole_video_spin.setValue(resolved.whole_video_max_sec)
        self.sample_duration_spin.setValue(resolved.sample_duration_sec)
        self.window_count_spin.setValue(resolved.sample_window_count)
        self.coarse_candidates_spin.setValue(resolved.coarse_max_candidates)
        self.exact_candidates_spin.setValue(resolved.exact_max_candidates)
        coarse_index = self.coarse_subsample_combo.findData(resolved.coarse_vmaf_subsample)
        if coarse_index >= 0:
            self.coarse_subsample_combo.setCurrentIndex(coarse_index)
        exact_index = self.exact_subsample_combo.findData(resolved.exact_vmaf_subsample)
        if exact_index >= 0:
            self.exact_subsample_combo.setCurrentIndex(exact_index)
        self.min_tolerance_spin.setValue(max(1, round(resolved.min_search_tolerance_bps / 1000)))
        self.search_ratio_spin.setValue(resolved.search_tolerance_ratio * 100.0)

    def settings(self) -> AnalysisProfileSettings:
        return validate_analysis_settings(
            AnalysisProfileSettings(
                whole_video_max_sec=float(self.whole_video_spin.value()),
                sample_duration_sec=float(self.sample_duration_spin.value()),
                sample_window_count=int(self.window_count_spin.value()),
                coarse_max_candidates=int(self.coarse_candidates_spin.value()),
                exact_max_candidates=int(self.exact_candidates_spin.value()),
                coarse_vmaf_subsample=int(self.coarse_subsample_combo.currentData() or 3),
                exact_vmaf_subsample=int(self.exact_subsample_combo.currentData() or 1),
                min_search_tolerance_bps=int(self.min_tolerance_spin.value()) * 1000,
                search_tolerance_ratio=float(self.search_ratio_spin.value()) / 100.0,
            )
        )

    def apply_translations(self, tr: Translator) -> None:
        self.whole_video_label.setText(tr.t("gui.label.whole_video_max_sec"))
        self.sample_duration_label.setText(tr.t("gui.label.sample_duration_sec"))
        self.window_count_label.setText(tr.t("gui.label.sample_window_count"))
        self.coarse_candidates_label.setText(tr.t("gui.label.coarse_max_candidates"))
        self.exact_candidates_label.setText(tr.t("gui.label.exact_max_candidates"))
        self.coarse_subsample_label.setText(tr.t("gui.label.coarse_vmaf_subsample"))
        self.exact_subsample_label.setText(tr.t("gui.label.exact_vmaf_subsample"))
        self.min_tolerance_label.setText(tr.t("gui.label.min_search_tolerance_kbps"))
        self.search_ratio_label.setText(tr.t("gui.label.search_tolerance_ratio"))
        self.reset_button.setText(tr.t("gui.button.reset_analysis_profile"))

    def _reset_to_factory(self) -> None:
        self.set_settings(FACTORY_ANALYSIS_PROFILES[self.profile_name])


class SettingsDialog(QDialog):
    def __init__(self, tr: Translator, settings: dict[str, object], parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self.redetect_requested = False
        self._profile_pages: dict[AnalysisProfileName, _AnalysisProfilePage] = {}
        self._build_ui()
        self._load_settings(settings)
        self.apply_translations(tr)

    def _build_ui(self) -> None:
        self.resize(760, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        general = QGridLayout()
        general.setHorizontalSpacing(10)
        general.setVerticalSpacing(10)

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

        general.addWidget(self.language_label, 0, 0)
        general.addWidget(self.language_combo, 0, 1, 1, 2)
        general.addWidget(self.workdir_label, 1, 0)
        general.addWidget(self.workdir_edit, 1, 1)
        general.addWidget(self.workdir_button, 1, 2)
        general.addWidget(self.ffmpeg_label, 2, 0)
        general.addWidget(self.ffmpeg_edit, 2, 1)
        general.addWidget(self.ffmpeg_button, 2, 2)
        general.addWidget(self.ffprobe_label, 3, 0)
        general.addWidget(self.ffprobe_edit, 3, 1)
        general.addWidget(self.ffprobe_button, 3, 2)
        general.addWidget(self.log_level_label, 4, 0)
        general.addWidget(self.log_level_combo, 4, 1, 1, 2)
        general.addWidget(self.keep_preview_temp_check, 5, 0, 1, 3)
        layout.addLayout(general)

        self.policy_group = QGroupBox()
        policy_layout = QGridLayout(self.policy_group)
        policy_layout.setHorizontalSpacing(10)
        policy_layout.setVerticalSpacing(8)
        self.size_blocked_policy_label = QLabel()
        self.size_blocked_policy_combo = QComboBox()
        self.quality_unreachable_policy_label = QLabel()
        self.quality_unreachable_policy_combo = QComboBox()
        self.skipped_output_policy_label = QLabel()
        self.skipped_output_policy_combo = QComboBox()
        policy_layout.addWidget(self.size_blocked_policy_label, 0, 0)
        policy_layout.addWidget(self.size_blocked_policy_combo, 0, 1)
        policy_layout.addWidget(self.quality_unreachable_policy_label, 1, 0)
        policy_layout.addWidget(self.quality_unreachable_policy_combo, 1, 1)
        policy_layout.addWidget(self.skipped_output_policy_label, 2, 0)
        policy_layout.addWidget(self.skipped_output_policy_combo, 2, 1)
        layout.addWidget(self.policy_group)

        self.analysis_group = QGroupBox()
        analysis_layout = QVBoxLayout(self.analysis_group)
        active_row = QHBoxLayout()
        self.analysis_profile_label = QLabel()
        self.analysis_profile_combo = QComboBox()
        for name in AnalysisProfileName:
            self.analysis_profile_combo.addItem("", name.value)
        active_row.addWidget(self.analysis_profile_label)
        active_row.addWidget(self.analysis_profile_combo, 1)
        analysis_layout.addLayout(active_row)

        self.analysis_tabs = QTabWidget()
        for name in AnalysisProfileName:
            page = _AnalysisProfilePage(name)
            self._profile_pages[name] = page
            self.analysis_tabs.addTab(page, "")
        analysis_layout.addWidget(self.analysis_tabs)
        layout.addWidget(self.analysis_group)

        self.redetect_button = QPushButton()
        layout.addWidget(self.redetect_button)
        layout.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(12, 0, 12, 12)
        buttons.addWidget(self.button_box)
        root.addLayout(buttons)

        self.workdir_button.clicked.connect(self._browse_workdir)
        self.ffmpeg_button.clicked.connect(self._browse_ffmpeg)
        self.ffprobe_button.clicked.connect(self._browse_ffprobe)
        self.redetect_button.clicked.connect(self._request_redetect)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.analysis_profile_combo.currentIndexChanged.connect(self._sync_active_profile_tab)

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
        self.size_blocked_policy_combo.clear()
        self.size_blocked_policy_combo.addItem("", SizeBlockedPolicy.RELAX_SIZE.value)
        self.size_blocked_policy_combo.addItem("", SizeBlockedPolicy.RELAX_QUALITY.value)
        self.size_blocked_policy_combo.addItem("", SizeBlockedPolicy.ASK.value)
        self.quality_unreachable_policy_combo.clear()
        self.quality_unreachable_policy_combo.addItem("", QualityUnreachablePolicy.SKIP.value)
        self.quality_unreachable_policy_combo.addItem("", QualityUnreachablePolicy.ASK.value)
        self.skipped_output_policy_combo.clear()
        self.skipped_output_policy_combo.addItem("", SkippedOutputPolicy.COPY.value)
        self.skipped_output_policy_combo.addItem("", SkippedOutputPolicy.ASK.value)
        self.skipped_output_policy_combo.addItem("", SkippedOutputPolicy.IGNORE.value)
        size_policy, unreachable_policy, skipped_policy = smart_policies_from_config(settings)
        size_index = self.size_blocked_policy_combo.findData(size_policy.value)
        if size_index >= 0:
            self.size_blocked_policy_combo.setCurrentIndex(size_index)
        unreachable_index = self.quality_unreachable_policy_combo.findData(unreachable_policy.value)
        if unreachable_index >= 0:
            self.quality_unreachable_policy_combo.setCurrentIndex(unreachable_index)
        skipped_index = self.skipped_output_policy_combo.findData(skipped_policy.value)
        if skipped_index >= 0:
            self.skipped_output_policy_combo.setCurrentIndex(skipped_index)

        stored_profiles = settings.get("analysis_profiles")
        profile_name = parse_analysis_profile_name(settings.get("analysis_profile"))
        profile_index = self.analysis_profile_combo.findData(profile_name.value)
        if profile_index >= 0:
            self.analysis_profile_combo.setCurrentIndex(profile_index)
        for name, page in self._profile_pages.items():
            page.set_settings(resolve_analysis_settings(name, stored_profiles))
        self._sync_active_profile_tab()

    def apply_translations(self, tr: Translator) -> None:
        self.tr = tr
        self.setWindowTitle(self.tr.t("gui.window.settings"))
        self.language_label.setText(self.tr.t("gui.label.language"))
        self.workdir_label.setText(self.tr.t("gui.label.workdir"))
        self.ffmpeg_label.setText(self.tr.t("gui.label.ffmpeg"))
        self.ffprobe_label.setText(self.tr.t("gui.label.ffprobe"))
        self.log_level_label.setText(self.tr.t("gui.label.log_level"))
        self.keep_preview_temp_check.setText(self.tr.t("gui.checkbox.keep_preview_temp"))
        self.policy_group.setTitle(self.tr.t("gui.group.smart_policies"))
        self.size_blocked_policy_label.setText(self.tr.t("gui.label.size_blocked_policy"))
        self.quality_unreachable_policy_label.setText(self.tr.t("gui.label.quality_unreachable_policy"))
        self.size_blocked_policy_combo.setItemText(
            self.size_blocked_policy_combo.findData(SizeBlockedPolicy.RELAX_SIZE.value),
            self.tr.t("gui.value.size_blocked_relax_size"),
        )
        self.size_blocked_policy_combo.setItemText(
            self.size_blocked_policy_combo.findData(SizeBlockedPolicy.RELAX_QUALITY.value),
            self.tr.t("gui.value.size_blocked_relax_quality"),
        )
        self.size_blocked_policy_combo.setItemText(
            self.size_blocked_policy_combo.findData(SizeBlockedPolicy.ASK.value),
            self.tr.t("gui.value.size_blocked_ask"),
        )
        self.quality_unreachable_policy_combo.setItemText(
            self.quality_unreachable_policy_combo.findData(QualityUnreachablePolicy.SKIP.value),
            self.tr.t("gui.value.quality_unreachable_skip"),
        )
        self.quality_unreachable_policy_combo.setItemText(
            self.quality_unreachable_policy_combo.findData(QualityUnreachablePolicy.ASK.value),
            self.tr.t("gui.value.quality_unreachable_ask"),
        )
        self.skipped_output_policy_label.setText(self.tr.t("gui.label.skipped_output_policy"))
        self.skipped_output_policy_combo.setItemText(
            self.skipped_output_policy_combo.findData(SkippedOutputPolicy.COPY.value),
            self.tr.t("gui.value.skipped_output_copy"),
        )
        self.skipped_output_policy_combo.setItemText(
            self.skipped_output_policy_combo.findData(SkippedOutputPolicy.ASK.value),
            self.tr.t("gui.value.skipped_output_ask"),
        )
        self.skipped_output_policy_combo.setItemText(
            self.skipped_output_policy_combo.findData(SkippedOutputPolicy.IGNORE.value),
            self.tr.t("gui.value.skipped_output_ignore"),
        )
        self.analysis_group.setTitle(self.tr.t("gui.group.analysis_scan"))
        self.analysis_profile_label.setText(self.tr.t("gui.label.analysis_active_profile"))
        self.analysis_profile_combo.setItemText(
            self.analysis_profile_combo.findData(AnalysisProfileName.FAST.value),
            self.tr.t("gui.value.analysis_fast"),
        )
        self.analysis_profile_combo.setItemText(
            self.analysis_profile_combo.findData(AnalysisProfileName.BALANCE.value),
            self.tr.t("gui.value.analysis_balance"),
        )
        self.analysis_profile_combo.setItemText(
            self.analysis_profile_combo.findData(AnalysisProfileName.PRECISE.value),
            self.tr.t("gui.value.analysis_precise"),
        )
        self.analysis_tabs.setTabText(0, self.tr.t("gui.value.analysis_fast"))
        self.analysis_tabs.setTabText(1, self.tr.t("gui.value.analysis_balance"))
        self.analysis_tabs.setTabText(2, self.tr.t("gui.value.analysis_precise"))
        for page in self._profile_pages.values():
            page.apply_translations(self.tr)
        self.workdir_button.setText(self.tr.t("gui.button.browse_dir"))
        self.ffmpeg_button.setText(self.tr.t("gui.button.browse_exe"))
        self.ffprobe_button.setText(self.tr.t("gui.button.browse_exe"))
        self.redetect_button.setText(self.tr.t("gui.button.redetect_encoders"))
        self.ffmpeg_edit.setPlaceholderText(self.tr.t("gui.placeholder.ffmpeg"))
        self.ffprobe_edit.setPlaceholderText(self.tr.t("gui.placeholder.ffprobe"))
        self.ffmpeg_edit.setToolTip(self.tr.t("gui.placeholder.ffmpeg"))
        self.ffprobe_edit.setToolTip(self.tr.t("gui.placeholder.ffprobe"))
        self.analysis_profile_combo.setToolTip(self.tr.t("gui.tooltip.analysis_profile"))

    def values(self) -> dict[str, object]:
        stored = all_analysis_profile_payloads()
        for name, page in self._profile_pages.items():
            stored[name.value] = analysis_settings_payload(page.settings())
        return {
            "language": self.language_combo.currentData(),
            "workdir_path": self.workdir_edit.text().strip(),
            "ffmpeg_path": self.ffmpeg_edit.text().strip(),
            "ffprobe_path": self.ffprobe_edit.text().strip(),
            "log_level": self.log_level_combo.currentText(),
            "keep_preview_temp": self.keep_preview_temp_check.isChecked(),
            "size_blocked_policy": self.size_blocked_policy_combo.currentData()
            or SizeBlockedPolicy.RELAX_SIZE.value,
            "quality_unreachable_policy": self.quality_unreachable_policy_combo.currentData()
            or QualityUnreachablePolicy.SKIP.value,
            "skipped_output_policy": self.skipped_output_policy_combo.currentData()
            or SkippedOutputPolicy.COPY.value,
            "analysis_profile": self.analysis_profile_combo.currentData()
            or AnalysisProfileName.BALANCE.value,
            "analysis_profiles": stored,
        }

    def _sync_active_profile_tab(self) -> None:
        name = parse_analysis_profile_name(self.analysis_profile_combo.currentData())
        index = list(AnalysisProfileName).index(name)
        self.analysis_tabs.setCurrentIndex(index)

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

    def _request_redetect(self) -> None:
        self.redetect_requested = True
        self.accept()
