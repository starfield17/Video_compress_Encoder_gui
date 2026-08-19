"""Deterministic Smart sample planning from lightweight scout metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable

from core.models import AnalysisProfileSettings


SI_WEIGHT = 0.45
TI_WEIGHT = 0.55
_EPSILON = 1e-9


class SamplePlanningError(ValueError):
    """The scout results cannot produce a valid, non-overlapping sample plan."""


@dataclass(frozen=True, slots=True)
class ScoutWindow:
    id: str
    start_sec: float
    duration_sec: float

    @property
    def center_sec(self) -> float:
        return self.start_sec + self.duration_sec / 2.0


@dataclass(frozen=True, slots=True)
class ScoutObservation:
    window: ScoutWindow
    si_p90: float
    ti_p90: float
    scene_cut_times: tuple[float, ...] = ()
    max_scene_score: float = 0.0


@dataclass(frozen=True, slots=True)
class PlannedWindow:
    id: str
    start_sec: float
    duration_sec: float
    reasons: tuple[str, ...]
    scout_id: str | None = None
    crosses_scene_cut: bool = False

    @property
    def center_sec(self) -> float:
        return self.start_sec + self.duration_sec / 2.0


@dataclass(frozen=True, slots=True)
class SamplePlan:
    scout_windows: tuple[ScoutWindow, ...]
    search_windows: tuple[PlannedWindow, ...]
    holdout_windows: tuple[PlannedWindow, ...]
    whole_video: bool


def planned_window_payload(window: PlannedWindow) -> dict[str, object]:
    return {
        "id": window.id,
        "start_sec": window.start_sec,
        "duration_sec": window.duration_sec,
        "reasons": list(window.reasons),
        "scout_id": window.scout_id,
        "crosses_scene_cut": window.crosses_scene_cut,
    }


def planned_window_from_payload(data: dict[str, object]) -> PlannedWindow:
    raw_reasons = data.get("reasons", [])
    reasons = tuple(str(value) for value in raw_reasons) if isinstance(raw_reasons, list) else ()
    return PlannedWindow(
        id=str(data["id"]),
        start_sec=float(str(data["start_sec"])),
        duration_sec=float(str(data["duration_sec"])),
        reasons=reasons,
        scout_id=None if data.get("scout_id") is None else str(data["scout_id"]),
        crosses_scene_cut=bool(data.get("crosses_scene_cut", False)),
    )


def scout_observation_payload(observation: ScoutObservation) -> dict[str, object]:
    return {
        "id": observation.window.id,
        "start_sec": observation.window.start_sec,
        "duration_sec": observation.window.duration_sec,
        "si_p90": observation.si_p90,
        "ti_p90": observation.ti_p90,
        "scene_cut_times": list(observation.scene_cut_times),
        "scene_cut_count": len(observation.scene_cut_times),
        "max_scene_score": observation.max_scene_score,
    }


def scout_observation_from_payload(data: dict[str, object]) -> ScoutObservation:
    raw_cuts = data.get("scene_cut_times", [])
    cuts = tuple(float(str(value)) for value in raw_cuts) if isinstance(raw_cuts, list) else ()
    return ScoutObservation(
        window=ScoutWindow(
            id=str(data["id"]),
            start_sec=float(str(data["start_sec"])),
            duration_sec=float(str(data["duration_sec"])),
        ),
        si_p90=float(str(data["si_p90"])),
        ti_p90=float(str(data["ti_p90"])),
        scene_cut_times=cuts,
        max_scene_score=float(str(data.get("max_scene_score", 0.0))),
    )


@dataclass(frozen=True, slots=True)
class RankedScoutObservation:
    observation: ScoutObservation
    si_rank: float
    ti_rank: float
    difficulty: float


def _configured_search_window_count(
    duration_sec: float, settings: AnalysisProfileSettings
) -> int:
    if duration_sec < 10 * 60:
        return int(settings.sample_count_under_10m)
    if duration_sec < 60 * 60:
        return int(settings.sample_count_10_to_60m)
    if duration_sec < 180 * 60:
        return int(settings.sample_count_60_to_180m)
    return int(settings.sample_count_over_180m)


def search_window_count(duration_sec: float, settings: AnalysisProfileSettings) -> int:
    configured = _configured_search_window_count(duration_sec, settings)
    capacity = max(1, int(math.floor(duration_sec / settings.sample_duration_sec)))
    return max(1, min(int(configured), capacity))


def holdout_window_count(duration_sec: float, settings: AnalysisProfileSettings) -> int:
    if duration_sec > 180 * 60:
        return int(settings.holdout_window_count_over_180m)
    return int(settings.holdout_window_count)


def should_analyze_whole_video(
    duration_sec: float, settings: AnalysisProfileSettings
) -> bool:
    """Return whether the selected confidence profile requires full-source analysis."""

    return duration_sec <= settings.whole_video_max_sec


def plan_scout_windows(duration_sec: float, settings: AnalysisProfileSettings) -> tuple[ScoutWindow, ...]:
    """Return evenly distributed low-cost scout windows."""

    if not math.isfinite(duration_sec) or duration_sec <= 0:
        raise SamplePlanningError("Source duration must be finite and positive.")
    if should_analyze_whole_video(duration_sec, settings):
        return ()
    search_count = search_window_count(duration_sec, settings)
    requested = min(settings.scout_max_windows, search_count * settings.scout_multiplier)
    count = max(1, int(requested))
    scout_duration = min(float(settings.scout_duration_sec), duration_sec)
    max_start = max(0.0, duration_sec - scout_duration)
    if count == 1:
        starts = [max_start / 2.0]
    else:
        starts = [max_start * index / (count - 1) for index in range(count)]
    return tuple(
        ScoutWindow(id=f"scout-{index + 1:03d}", start_sec=start, duration_sec=scout_duration)
        for index, start in enumerate(starts)
    )


def _midrank_percentiles(values: list[float]) -> list[float]:
    if not values or any(not math.isfinite(value) for value in values):
        raise SamplePlanningError("Scout metrics must be finite and non-empty.")
    count = len(values)
    result = [0.0] * count
    by_value = sorted(range(count), key=lambda index: (values[index], index))
    position = 0
    while position < count:
        end = position + 1
        while end < count and values[by_value[end]] == values[by_value[position]]:
            end += 1
        # Midrank percentiles use [0, 1].  A singleton video is not planned
        # through this path, but keeping its value defined makes the helper sane.
        rank = 0.5 if count == 1 else ((position + end - 1) / 2.0) / (count - 1)
        for sorted_index in range(position, end):
            result[by_value[sorted_index]] = rank
        position = end
    return result


def rank_scout_observations(observations: Iterable[ScoutObservation]) -> tuple[RankedScoutObservation, ...]:
    items = tuple(observations)
    if not items:
        raise SamplePlanningError("Long videos require at least one scout observation.")
    ids = [item.window.id for item in items]
    if len(set(ids)) != len(ids):
        raise SamplePlanningError("Scout window IDs must be unique.")
    si_ranks = _midrank_percentiles([item.si_p90 for item in items])
    ti_ranks = _midrank_percentiles([item.ti_p90 for item in items])
    return tuple(
        RankedScoutObservation(
            observation=item,
            si_rank=si_ranks[index],
            ti_rank=ti_ranks[index],
            difficulty=SI_WEIGHT * si_ranks[index] + TI_WEIGHT * ti_ranks[index],
        )
        for index, item in enumerate(items)
    )


def ranked_scout_payloads(observations: Iterable[ScoutObservation]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for ranked in rank_scout_observations(observations):
        payload = scout_observation_payload(ranked.observation)
        payload.update(
            {
                "si_rank": ranked.si_rank,
                "ti_rank": ranked.ti_rank,
                "difficulty": ranked.difficulty,
            }
        )
        payloads.append(payload)
    return payloads


def _project_window(
    observation: RankedScoutObservation,
    *,
    duration_sec: float,
    sample_duration_sec: float,
    kind: str,
    reasons: Iterable[str],
) -> PlannedWindow:
    sample_duration = min(sample_duration_sec, duration_sec)
    start = min(
        max(0.0, observation.observation.window.center_sec - sample_duration / 2.0),
        max(0.0, duration_sec - sample_duration),
    )
    return PlannedWindow(
        id=f"{kind}:{observation.observation.window.id}",
        start_sec=start,
        duration_sec=sample_duration,
        reasons=tuple(dict.fromkeys(reasons)),
        scout_id=observation.observation.window.id,
    )


def _overlaps(left: PlannedWindow, right: PlannedWindow) -> bool:
    return left.start_sec < right.start_sec + right.duration_sec - _EPSILON and right.start_sec < left.start_sec + left.duration_sec - _EPSILON


def _add_window(
    selected: list[PlannedWindow],
    candidate: PlannedWindow,
    *,
    limit: int,
    allow_merge: bool = True,
) -> bool:
    existing_index = next(
        (
            index
            for index, window in enumerate(selected)
            if window.scout_id == candidate.scout_id
        ),
        None,
    )
    if existing_index is not None:
        if not allow_merge:
            return False
        existing = selected[existing_index]
        selected[existing_index] = replace(
            existing, reasons=tuple(dict.fromkeys((*existing.reasons, *candidate.reasons)))
        )
        return True
    overlap_index = next(
        (index for index, window in enumerate(selected) if _overlaps(candidate, window)),
        None,
    )
    if overlap_index is not None:
        if not allow_merge:
            return False
        existing = selected[overlap_index]
        selected[overlap_index] = replace(
            existing,
            reasons=tuple(dict.fromkeys((*existing.reasons, *candidate.reasons))),
        )
        return True
    if len(selected) >= limit:
        return False
    selected.append(candidate)
    return True


def _available_window_capacity(
    ranked: tuple[RankedScoutObservation, ...],
    protected: list[PlannedWindow],
    *,
    duration_sec: float,
    sample_duration_sec: float,
) -> int:
    """Return the maximum remaining non-overlapping projected-window count."""

    used_scout_ids = {window.scout_id for window in protected}
    candidates = [
        _project_window(
            item,
            duration_sec=duration_sec,
            sample_duration_sec=sample_duration_sec,
            kind="capacity",
            reasons=(),
        )
        for item in ranked
        if item.observation.window.id not in used_scout_ids
    ]
    candidates = [
        candidate
        for candidate in candidates
        if not any(_overlaps(candidate, window) for window in protected)
    ]
    candidates.sort(
        key=lambda window: (
            window.start_sec + window.duration_sec,
            window.start_sec,
            window.id,
        )
    )
    selected: list[PlannedWindow] = []
    for candidate in candidates:
        if not selected or not _overlaps(candidate, selected[-1]):
            selected.append(candidate)
    return len(selected)


def _ranked_desc(
    items: Iterable[RankedScoutObservation],
    key: str,
    *,
    anchor_sec: float | None = None,
) -> list[RankedScoutObservation]:
    def tie_breaker(item: RankedScoutObservation) -> tuple[float, float, str]:
        distance = (
            0.0
            if anchor_sec is None
            else abs(item.observation.window.center_sec - anchor_sec)
        )
        return (distance, item.observation.window.start_sec, item.observation.window.id)

    if key == "si":
        return sorted(items, key=lambda item: (-item.si_rank, *tie_breaker(item)))
    if key == "ti":
        return sorted(items, key=lambda item: (-item.ti_rank, *tie_breaker(item)))
    return sorted(items, key=lambda item: (-item.difficulty, *tie_breaker(item)))


def _coverage_candidates(
    ranked: tuple[RankedScoutObservation, ...], bins: int, duration_sec: float
) -> list[tuple[list[RankedScoutObservation], str, float]]:
    choices: list[tuple[list[RankedScoutObservation], str, float]] = []
    for bin_index in range(bins):
        low = duration_sec * bin_index / bins
        high = duration_sec * (bin_index + 1) / bins
        within = [
            item
            for item in ranked
            if low <= item.observation.window.center_sec < high
            or (bin_index == bins - 1 and item.observation.window.center_sec <= high)
        ]
        if within:
            midpoint = (low + high) / 2.0
            choices.append(
                (
                    _ranked_desc(within, "difficulty", anchor_sec=midpoint),
                    f"coverage_bin_{bin_index + 1}",
                    midpoint,
                )
            )
    return choices


def _select_holdouts(
    ranked: tuple[RankedScoutObservation, ...],
    search: list[PlannedWindow],
    *,
    duration_sec: float,
    sample_duration_sec: float,
    count: int,
) -> list[PlannedWindow]:
    holdouts: list[PlannedWindow] = []
    if count <= 0:
        return holdouts
    available = [
        item
        for item in _ranked_desc(ranked, "difficulty", anchor_sec=duration_sec / 2.0)
        if item.observation.window.id not in {window.scout_id for window in search}
    ]
    while available and len(holdouts) < count:
        anchors = [window.center_sec for window in (*search, *holdouts)]

        def score(item: RankedScoutObservation) -> tuple[float, float, float, str]:
            distance = min((abs(item.observation.window.center_sec - anchor) for anchor in anchors), default=duration_sec)
            diversity = min(1.0, distance / max(duration_sec / max(count + len(search), 1), 1.0))
            return (
                0.70 * item.difficulty + 0.30 * diversity,
                item.difficulty,
                -item.observation.window.start_sec,
                item.observation.window.id,
            )

        candidate_ranked = max(available, key=score)
        candidate = _project_window(
            candidate_ranked,
            duration_sec=duration_sec,
            sample_duration_sec=sample_duration_sec,
            kind="holdout",
            reasons=("holdout_difficulty_and_diversity",),
        )
        available.remove(candidate_ranked)
        if any(_overlaps(candidate, window) for window in (*search, *holdouts)):
            continue
        remaining = count - len(holdouts) - 1
        if _available_window_capacity(
            ranked,
            [*search, *holdouts, candidate],
            duration_sec=duration_sec,
            sample_duration_sec=sample_duration_sec,
        ) < remaining:
            continue
        holdouts.append(candidate)
    return holdouts


def build_sample_plan(
    duration_sec: float,
    settings: AnalysisProfileSettings,
    observations: Iterable[ScoutObservation] = (),
) -> SamplePlan:
    """Select hard and timeline-representative search/holdout windows."""

    if not math.isfinite(duration_sec) or duration_sec <= 0:
        raise SamplePlanningError("Source duration must be finite and positive.")
    if should_analyze_whole_video(duration_sec, settings):
        whole = PlannedWindow(
            id="search:whole-video", start_sec=0.0, duration_sec=duration_sec, reasons=("whole_video",)
        )
        return SamplePlan((), (whole,), (), True)

    ranked = rank_scout_observations(observations)
    expected = {window.id for window in plan_scout_windows(duration_sec, settings)}
    actual = {item.observation.window.id for item in ranked}
    if actual != expected:
        raise SamplePlanningError("Scout observations do not match the deterministic scout plan.")

    expected_holdouts = holdout_window_count(duration_sec, settings)
    projected_capacity = _available_window_capacity(
        ranked,
        [],
        duration_sec=duration_sec,
        sample_duration_sec=settings.sample_duration_sec,
    )
    target = min(
        search_window_count(duration_sec, settings),
        projected_capacity - expected_holdouts,
    )
    if target < 1:
        raise SamplePlanningError(
            "Unable to reserve independent search and holdout windows."
        )
    search: list[PlannedWindow] = []

    def add_search(candidate: PlannedWindow, *, allow_merge: bool = True) -> bool:
        trial = list(search)
        if not _add_window(
            trial,
            candidate,
            limit=target,
            allow_merge=allow_merge,
        ):
            return False
        if len(trial) > len(search):
            remaining = target - len(trial) + expected_holdouts
            if _available_window_capacity(
                ranked,
                trial,
                duration_sec=duration_sec,
                sample_duration_sec=settings.sample_duration_sec,
            ) < remaining:
                return False
        search[:] = trial
        return True

    # Preserve the three distinct compression-risk representatives first.  On
    # very small K this intentionally outweighs the approximate 50/50 split.
    representatives = (
        ("si", "highest_si", 0.25),
        ("ti", "highest_ti", 0.75),
        ("difficulty", "global_hardest", 0.50),
    )
    for key, reason, anchor_fraction in representatives:
        candidate = _project_window(
            _ranked_desc(
                ranked,
                key,
                anchor_sec=duration_sec * anchor_fraction,
            )[0],
            duration_sec=duration_sec,
            sample_duration_sec=settings.sample_duration_sec,
            kind="search",
            reasons=(reason,),
        )
        add_search(candidate)

    coverage_slots = target // 2
    for candidates, reason, midpoint in _coverage_candidates(
        ranked, coverage_slots, duration_sec
    ):
        added = False
        for candidate_ranked in candidates:
            if add_search(
                _project_window(
                    candidate_ranked,
                    duration_sec=duration_sec,
                    sample_duration_sec=settings.sample_duration_sec,
                    kind="search",
                    reasons=(reason,),
                ),
                allow_merge=False,
            ):
                added = True
                break
        if not added and search:
            nearest_index = min(
                range(len(search)),
                key=lambda index: (
                    abs(search[index].center_sec - midpoint),
                    search[index].start_sec,
                    search[index].id,
                ),
            )
            existing = search[nearest_index]
            search[nearest_index] = replace(
                existing,
                reasons=tuple(dict.fromkeys((*existing.reasons, reason))),
            )

    for candidate_ranked in _ranked_desc(
        ranked, "difficulty", anchor_sec=duration_sec / 2.0
    ):
        if len(search) >= target:
            break
        add_search(
            _project_window(
                candidate_ranked,
                duration_sec=duration_sec,
                sample_duration_sec=settings.sample_duration_sec,
                kind="search",
                reasons=("hardship",),
            ),
        )
    if len(search) != target:
        raise SamplePlanningError("Unable to select the requested number of non-overlapping search windows.")
    search.sort(key=lambda window: (window.start_sec, window.id))

    holdouts = _select_holdouts(
        ranked,
        search,
        duration_sec=duration_sec,
        sample_duration_sec=settings.sample_duration_sec,
        count=expected_holdouts,
    )
    if len(holdouts) != expected_holdouts:
        raise SamplePlanningError(
            "Unable to select the requested number of independent holdout windows."
        )
    holdouts.sort(key=lambda window: (window.start_sec, window.id))
    return SamplePlan(
        scout_windows=tuple(item.observation.window for item in ranked),
        search_windows=tuple(search),
        holdout_windows=tuple(holdouts),
        whole_video=False,
    )


def align_window_to_scene_cuts(
    window: PlannedWindow,
    scene_cut_times: Iterable[float],
    source_duration_sec: float,
) -> PlannedWindow:
    """Move a window into a nearby shot where its duration allows it.

    No scene cut is treated as crossing when it lies exactly on a window edge.
    If no shot can contain the desired duration, the original window is kept
    and explicitly marked for receipt/log visibility.
    """

    if not math.isfinite(source_duration_sec) or source_duration_sec <= 0:
        raise SamplePlanningError("Source duration must be finite and positive.")
    cuts = sorted(
        {
            float(cut)
            for cut in scene_cut_times
            if math.isfinite(float(cut)) and _EPSILON < float(cut) < source_duration_sec - _EPSILON
        }
    )
    end = window.start_sec + window.duration_sec
    if not any(window.start_sec + _EPSILON < cut < end - _EPSILON for cut in cuts):
        return replace(window, crosses_scene_cut=False)
    boundaries = [0.0, *cuts, source_duration_sec]
    viable: list[tuple[float, float]] = []
    for left, right in zip(boundaries, boundaries[1:]):
        if right - left + _EPSILON >= window.duration_sec:
            viable.append((left, right))
    if not viable:
        return replace(window, crosses_scene_cut=True)
    desired_center = window.center_sec

    def placement(shot: tuple[float, float]) -> tuple[float, float]:
        left, right = shot
        start = min(max(window.start_sec, left), right - window.duration_sec)
        return start, abs((start + window.duration_sec / 2.0) - desired_center)

    start, _distance = min((placement(shot) for shot in viable), key=lambda item: (item[1], item[0]))
    return replace(window, start_sec=start, crosses_scene_cut=False)
