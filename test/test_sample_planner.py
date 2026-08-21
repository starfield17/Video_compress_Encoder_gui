from __future__ import annotations

import unittest

from core.smart.profiles import FACTORY_ANALYSIS_PROFILES
from core.models import AnalysisProfileName, AnalysisProfileSettings
from core.smart.sampling.planner import (
    PlannedWindow,
    ScoutObservation,
    align_window_to_scene_cuts,
    build_sample_plan,
    plan_scout_windows,
    rank_scout_observations,
    search_window_count,
    should_analyze_whole_video,
)


def _observations(duration: float, settings: AnalysisProfileSettings) -> tuple[ScoutObservation, ...]:
    windows = plan_scout_windows(duration, settings)
    return tuple(
        ScoutObservation(window, si_p90=float(index % 5), ti_p90=float((index * 3) % 7))
        for index, window in enumerate(windows)
    )


class SamplePlannerTest(unittest.TestCase):
    def test_profile_duration_buckets(self) -> None:
        settings = FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.BALANCE]
        self.assertEqual(search_window_count(9 * 60, settings), 4)
        self.assertEqual(search_window_count(10 * 60, settings), 5)
        self.assertEqual(search_window_count(60 * 60, settings), 6)
        self.assertEqual(search_window_count(180 * 60, settings), 6)

    def test_scouts_are_uniform_and_non_overlapping(self) -> None:
        settings = FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.BALANCE]
        windows = plan_scout_windows(2 * 60 * 60, settings)
        self.assertEqual(len(windows), 24)
        for left, right in zip(windows, windows[1:]):
            self.assertLessEqual(left.start_sec + left.duration_sec, right.start_sec)

    def test_midrank_and_plan_reasons_are_deterministic(self) -> None:
        settings = FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.BALANCE]
        observations = _observations(2 * 60 * 60, settings)
        ranked = rank_scout_observations(observations)
        self.assertEqual(len(ranked), len(observations))
        self.assertTrue(all(0.0 <= item.si_rank <= 1.0 for item in ranked))
        self.assertTrue(all(0.0 <= item.ti_rank <= 1.0 for item in ranked))
        tied = rank_scout_observations(
            (
                ScoutObservation(plan_scout_windows(120.0, settings)[0], 1.0, 2.0),
                ScoutObservation(plan_scout_windows(120.0, settings)[1], 1.0, 4.0),
            )
        )
        self.assertEqual((tied[0].si_rank, tied[1].si_rank), (0.5, 0.5))
        plan = build_sample_plan(2 * 60 * 60, settings, observations)
        self.assertEqual(len(plan.search_windows), 6)
        self.assertEqual(len(plan.holdout_windows), 2)
        reasons = {reason for window in plan.search_windows for reason in window.reasons}
        self.assertIn("highest_si", reasons)
        self.assertIn("highest_ti", reasons)
        self.assertIn("global_hardest", reasons)
        self.assertTrue(any(reason.startswith("coverage_bin_") for reason in reasons))
        all_windows = (*plan.search_windows, *plan.holdout_windows)
        self.assertEqual(len({window.id for window in all_windows}), len(all_windows))
        for left, right in zip(sorted(all_windows, key=lambda item: item.start_sec), sorted(all_windows, key=lambda item: item.start_sec)[1:]):
            self.assertLessEqual(left.start_sec + left.duration_sec, right.start_sec)

    def test_short_video_is_one_whole_search_window(self) -> None:
        settings = FACTORY_ANALYSIS_PROFILES[AnalysisProfileName.BALANCE]
        plan = build_sample_plan(20.0, settings)
        self.assertTrue(plan.whole_video)
        self.assertEqual(len(plan.search_windows), 1)
        self.assertFalse(plan.holdout_windows)
        self.assertEqual(plan.search_windows[0].reasons, ("whole_video",))

    def test_near_threshold_video_preserves_holdouts_without_planner_failure(
        self,
    ) -> None:
        thresholds = {
            AnalysisProfileName.FAST: 12.1,
            AnalysisProfileName.BALANCE: 20.1,
            AnalysisProfileName.PRECISE: 30.1,
        }
        for name, duration in thresholds.items():
            with self.subTest(profile=name.value):
                settings = FACTORY_ANALYSIS_PROFILES[name]
                self.assertFalse(should_analyze_whole_video(duration, settings))
                scouts = plan_scout_windows(duration, settings)
                observations = tuple(
                    ScoutObservation(window, si_p90=1.0, ti_p90=1.0)
                    for window in scouts
                )
                plan = build_sample_plan(duration, settings, observations)
                self.assertFalse(plan.whole_video)
                self.assertGreaterEqual(len(plan.search_windows), 1)
                self.assertEqual(
                    len(plan.holdout_windows), settings.holdout_window_count
                )

    def test_equal_complexity_metrics_keep_coverage_and_holdout_budget(self) -> None:
        duration = 120.0
        for name in AnalysisProfileName:
            with self.subTest(profile=name.value):
                settings = FACTORY_ANALYSIS_PROFILES[name]
                observations = tuple(
                    ScoutObservation(window, si_p90=1.0, ti_p90=1.0)
                    for window in plan_scout_windows(duration, settings)
                )
                plan = build_sample_plan(duration, settings, observations)
                self.assertEqual(
                    len(plan.search_windows),
                    search_window_count(duration, settings),
                )
                self.assertEqual(
                    len(plan.holdout_windows), settings.holdout_window_count
                )
                self.assertLess(
                    min(window.center_sec for window in plan.search_windows),
                    duration * 0.4,
                )
                self.assertGreater(
                    max(window.center_sec for window in plan.search_windows),
                    duration * 0.6,
                )
                all_windows = (*plan.search_windows, *plan.holdout_windows)
                ordered = sorted(all_windows, key=lambda item: item.start_sec)
                for left, right in zip(ordered, ordered[1:]):
                    self.assertLessEqual(
                        left.start_sec + left.duration_sec, right.start_sec
                    )

    def test_scene_alignment_avoids_cut_when_a_shot_can_fit(self) -> None:
        window = PlannedWindow("search:1", 8.0, 5.0, ("coverage",))
        aligned = align_window_to_scene_cuts(window, (10.0, 20.0), 30.0)
        self.assertFalse(aligned.crosses_scene_cut)
        self.assertFalse(aligned.start_sec < 10.0 < aligned.start_sec + aligned.duration_sec)

    def test_scene_alignment_records_unavoidable_crossing(self) -> None:
        window = PlannedWindow("search:1", 2.0, 8.0, ("coverage",))
        aligned = align_window_to_scene_cuts(window, (3.0, 6.0, 9.0), 12.0)
        self.assertTrue(aligned.crosses_scene_cut)
        self.assertEqual(aligned.start_sec, 2.0)


if __name__ == "__main__":
    unittest.main()
