from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from cli.cli_entry import _build_parser, _options_from_args
from core.analysis_profiles import (
    FACTORY_ANALYSIS_PROFILES,
    all_analysis_profile_payloads,
    analysis_profiles_from_config,
    bind_analysis_profile,
    parse_analysis_profile_name,
    resolve_analysis_settings,
    validate_analysis_settings,
)
from core.i18n import get_translator
from core.models import (
    AnalysisProfileName,
    AnalysisProfileSettings,
    EncodeOptions,
    SizeBlockedPolicy,
)
from core.preset_store import _default_app_config
from core.smart_quality import choose_smart_sample_windows
from gui.gui_mainwindow import MainWindow
from gui.settings_dialog import SettingsDialog


class AnalysisProfileCoreTestCase(unittest.TestCase):
    def test_factory_profiles_are_distinct(self) -> None:
        fast = FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.FAST]
        balance = FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.BALANCE]
        precise = FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.PRECISE]
        self.assertLess(fast.sample_duration_sec, balance.sample_duration_sec)
        self.assertLess(balance.sample_duration_sec, precise.sample_duration_sec)
        self.assertLess(fast.coarse_max_candidates, precise.coarse_max_candidates)
        self.assertGreater(fast.search_tolerance_ratio, precise.search_tolerance_ratio)

    def test_unknown_name_falls_back_to_balance(self) -> None:
        self.assertEqual(parse_analysis_profile_name(AnalysisProfileName.FAST), AnalysisProfileName.FAST)
        self.assertEqual(parse_analysis_profile_name("turbo"), AnalysisProfileName.BALANCE)
        name, settings = analysis_profiles_from_config({})
        self.assertEqual(name, AnalysisProfileName.BALANCE)
        self.assertEqual(settings, FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.BALANCE])

    def test_stored_overrides_merge_onto_factory(self) -> None:
        settings = resolve_analysis_settings(
            AnalysisProfileName.FAST,
            {"fast": {"sample_duration_sec": 4.5, "coarse_max_candidates": 5}},
        )
        self.assertEqual(settings.sample_duration_sec, 4.5)
        self.assertEqual(settings.coarse_max_candidates, 5)
        self.assertEqual(settings.whole_video_max_sec, 8.0)

    def test_even_subsample_is_coerced_to_odd(self) -> None:
        settings = validate_analysis_settings(
            AnalysisProfileSettings(coarse_vmaf_subsample=4, exact_vmaf_subsample=2)
        )
        self.assertEqual(settings.coarse_vmaf_subsample, 3)
        self.assertEqual(settings.exact_vmaf_subsample, 1)

    def test_exact_search_always_allows_budget_and_ceiling_evidence(self) -> None:
        settings = validate_analysis_settings(AnalysisProfileSettings(exact_max_candidates=1))
        self.assertEqual(settings.exact_max_candidates, 2)
        self.assertEqual(
            FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.FAST].exact_max_candidates,
            2,
        )

    def test_bind_analysis_profile_snapshots_settings(self) -> None:
        options = bind_analysis_profile(
            EncodeOptions(),
            name="precise",
            stored_profiles={"precise": {"exact_max_candidates": 7}},
        )
        self.assertEqual(options.analysis_profile, AnalysisProfileName.PRECISE)
        self.assertEqual(options.analysis_settings.exact_max_candidates, 7)
        self.assertEqual(options.analysis_settings.sample_duration_sec, 8.0)

    def test_fast_sample_windows_use_shorter_clips(self) -> None:
        windows = choose_smart_sample_windows(
            60.0,
            FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.FAST],
        )
        self.assertEqual(len(windows), 3)
        for window in windows:
            self.assertAlmostEqual(window.duration_sec, 3.0)

    def test_single_sample_window_is_centered(self) -> None:
        settings = AnalysisProfileSettings(
            whole_video_max_sec=5.0,
            sample_duration_sec=10.0,
            sample_window_count=1,
        )
        windows = choose_smart_sample_windows(60.0, settings)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0].start_sec, 25.0)
        self.assertAlmostEqual(windows[0].duration_sec, 10.0)

    def test_default_app_config_includes_analysis_profile(self) -> None:
        data = _default_app_config()
        self.assertEqual(data["analysis_profile"], AnalysisProfileName.BALANCE.value)
        self.assertEqual(data["analysis_profiles"], {})
        self.assertEqual(all_analysis_profile_payloads()["balance"]["sample_duration_sec"], 5.0)


class AnalysisProfileCliTestCase(unittest.TestCase):
    def test_analysis_profile_flag_binds_factory_fast_settings(self) -> None:
        args = _build_parser().parse_args(["plan", "input.mp4", "--analysis-profile", "fast"])
        with (
            patch("cli.cli_entry.load_app_config", return_value={}),
            patch("cli.cli_entry._load_base_options", return_value=EncodeOptions()),
        ):
            options = _options_from_args(args, Path("."))
        self.assertEqual(options.analysis_profile, AnalysisProfileName.FAST)
        self.assertEqual(options.analysis_settings.sample_duration_sec, 3.0)
        self.assertEqual(options.analysis_settings.coarse_max_candidates, 3)


class AnalysisProfileGuiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.repo_root = Path(__file__).resolve().parent.parent

    def test_main_window_uses_scan_combo_instead_of_policy_dropdowns(self) -> None:
        window = MainWindow(self.repo_root, language="en")
        try:
            self.assertFalse(hasattr(window, "size_blocked_policy_combo"))
            self.assertFalse(hasattr(window, "quality_unreachable_policy_combo"))
            self.assertFalse(hasattr(window, "skipped_output_policy_combo"))
            self.assertGreaterEqual(window.analysis_profile_combo.count(), 3)
            balance_index = window.analysis_profile_combo.findData(AnalysisProfileName.BALANCE.value)
            self.assertEqual(window.analysis_profile_combo.itemText(balance_index), "Balance")

            window.app_config["size_blocked_policy"] = SizeBlockedPolicy.ASK.value
            window.app_config["analysis_profiles"] = {"fast": {"sample_duration_sec": 4.0}}
            fast_index = window.analysis_profile_combo.findData(AnalysisProfileName.FAST.value)
            with patch.object(window, "_save_app_config_preserving_capabilities") as save_config:
                window.analysis_profile_combo.setCurrentIndex(balance_index)
                save_config.reset_mock()
                window.analysis_profile_combo.setCurrentIndex(fast_index)
                save_config.assert_called_once()
            self.assertEqual(window.app_config["analysis_profile"], AnalysisProfileName.FAST.value)
            options = window._current_options()
            self.assertEqual(options.size_blocked_policy, SizeBlockedPolicy.ASK)
            self.assertEqual(options.analysis_profile, AnalysisProfileName.FAST)
            self.assertEqual(options.analysis_settings.sample_duration_sec, 4.0)
        finally:
            window.close()

    def test_initial_state_restores_profile_before_persistence_is_enabled(self) -> None:
        config = _default_app_config()
        config["analysis_profile"] = AnalysisProfileName.PRECISE.value
        with (
            patch("gui.gui_mainwindow.load_app_config", return_value=config),
            patch("gui.gui_mainwindow.update_app_config") as update_config,
        ):
            window = MainWindow(self.repo_root, language="en")
        try:
            self.assertEqual(
                window.analysis_profile_combo.currentData(),
                AnalysisProfileName.PRECISE.value,
            )
            self.assertEqual(
                window.app_config["analysis_profile"],
                AnalysisProfileName.PRECISE.value,
            )
            update_config.assert_not_called()
        finally:
            window.close()

    def test_settings_dialog_edits_each_profile(self) -> None:
        tr = get_translator("en", self.repo_root / "config")
        dialog = SettingsDialog(
            tr,
            {
                "language": "en",
                "analysis_profile": "precise",
                "analysis_profiles": {"precise": {"sample_duration_sec": 9.0}},
            },
        )
        try:
            self.assertEqual(dialog.analysis_profile_combo.currentData(), "precise")
            precise_page = dialog._profile_pages[AnalysisProfileName.PRECISE]
            self.assertAlmostEqual(precise_page.sample_duration_spin.value(), 9.0)
            precise_page.sample_duration_spin.setValue(7.5)
            precise_page.coarse_candidates_spin.setValue(8)
            values = dialog.values()
            self.assertEqual(values["analysis_profile"], "precise")
            profiles = values["analysis_profiles"]
            assert isinstance(profiles, dict)
            self.assertEqual(profiles["precise"]["sample_duration_sec"], 7.5)
            self.assertEqual(profiles["precise"]["coarse_max_candidates"], 8)
            self.assertEqual(profiles["balance"]["sample_duration_sec"], 5.0)
            precise_page._reset_to_factory()
            reset = dialog.values()["analysis_profiles"]
            assert isinstance(reset, dict)
            self.assertEqual(reset["precise"]["sample_duration_sec"], 8.0)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
