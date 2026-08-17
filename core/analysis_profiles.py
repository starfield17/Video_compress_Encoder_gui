from __future__ import annotations

from dataclasses import asdict, fields, replace

from core.models import AnalysisProfileName, AnalysisProfileSettings, EncodeOptions
from core.vmaf_runtime import validate_vmaf_subsample


FACTORY_ANALYSIS_PROFILES: dict[AnalysisProfileName, AnalysisProfileSettings] = {
    AnalysisProfileName.FAST: AnalysisProfileSettings(
        whole_video_max_sec=8.0,
        sample_duration_sec=3.0,
        sample_window_count=3,
        coarse_max_candidates=3,
        exact_max_candidates=2,
        coarse_vmaf_subsample=3,
        exact_vmaf_subsample=1,
        min_search_tolerance_bps=80_000,
        search_tolerance_ratio=0.05,
    ),
    AnalysisProfileName.BALANCE: AnalysisProfileSettings(),
    AnalysisProfileName.PRECISE: AnalysisProfileSettings(
        whole_video_max_sec=15.0,
        sample_duration_sec=8.0,
        sample_window_count=3,
        coarse_max_candidates=6,
        exact_max_candidates=4,
        coarse_vmaf_subsample=3,
        exact_vmaf_subsample=1,
        min_search_tolerance_bps=25_000,
        search_tolerance_ratio=0.02,
    ),
}

_INT_FIELDS = {
    "sample_window_count",
    "coarse_max_candidates",
    "exact_max_candidates",
    "coarse_vmaf_subsample",
    "exact_vmaf_subsample",
    "min_search_tolerance_bps",
}


def parse_analysis_profile_name(value: object) -> AnalysisProfileName:
    if isinstance(value, AnalysisProfileName):
        return value
    try:
        return AnalysisProfileName(str(value))
    except ValueError:
        return AnalysisProfileName.BALANCE


def _odd_subsample(value: object, default: int) -> int:
    try:
        raw = int(float(str(value)))
    except (TypeError, ValueError):
        raw = default
    if raw < 1:
        raw = 1
    if raw % 2 == 0:
        raw -= 1
    return validate_vmaf_subsample(max(1, raw))


def _normalized_settings(data: object, base: AnalysisProfileSettings) -> AnalysisProfileSettings:
    if not isinstance(data, dict):
        return validate_analysis_settings(base)
    updates: dict[str, object] = {}
    for field in fields(AnalysisProfileSettings):
        if field.name not in data:
            continue
        raw = data[field.name]
        try:
            if field.name in _INT_FIELDS:
                updates[field.name] = int(raw)
            else:
                updates[field.name] = float(raw)
        except (TypeError, ValueError):
            continue
    merged = replace(base, **updates)  # type: ignore[arg-type]
    return validate_analysis_settings(merged)


def validate_analysis_settings(settings: AnalysisProfileSettings) -> AnalysisProfileSettings:
    whole = max(1.0, float(settings.whole_video_max_sec))
    sample = max(1.0, float(settings.sample_duration_sec))
    windows = min(5, max(1, int(settings.sample_window_count)))
    coarse_candidates = min(8, max(1, int(settings.coarse_max_candidates)))
    exact_candidates = min(8, max(2, int(settings.exact_max_candidates)))
    coarse_sub = _odd_subsample(settings.coarse_vmaf_subsample, 3)
    exact_sub = _odd_subsample(settings.exact_vmaf_subsample, 1)
    min_tol = max(1_000, int(settings.min_search_tolerance_bps))
    ratio = min(0.25, max(0.005, float(settings.search_tolerance_ratio)))
    return AnalysisProfileSettings(
        whole_video_max_sec=whole,
        sample_duration_sec=sample,
        sample_window_count=windows,
        coarse_max_candidates=coarse_candidates,
        exact_max_candidates=exact_candidates,
        coarse_vmaf_subsample=coarse_sub,
        exact_vmaf_subsample=exact_sub,
        min_search_tolerance_bps=min_tol,
        search_tolerance_ratio=ratio,
    )


def resolve_analysis_settings(
    name: AnalysisProfileName,
    stored_profiles: object = None,
) -> AnalysisProfileSettings:
    factory = FACTORY_ANALYSIS_PROFILES[name]
    overrides = None
    if isinstance(stored_profiles, dict):
        overrides = stored_profiles.get(name.value)
    return _normalized_settings(overrides, factory)


def analysis_profiles_from_config(data: dict[str, object]) -> tuple[AnalysisProfileName, AnalysisProfileSettings]:
    name = parse_analysis_profile_name(data.get("analysis_profile", AnalysisProfileName.BALANCE.value))
    return name, resolve_analysis_settings(name, data.get("analysis_profiles"))


def analysis_settings_payload(settings: AnalysisProfileSettings) -> dict[str, float | int]:
    return asdict(validate_analysis_settings(settings))


def all_analysis_profile_payloads(stored_profiles: object = None) -> dict[str, dict[str, float | int]]:
    return {
        name.value: analysis_settings_payload(resolve_analysis_settings(name, stored_profiles))
        for name in AnalysisProfileName
    }


def bind_analysis_profile(
    options: EncodeOptions,
    *,
    name: object = None,
    stored_profiles: object = None,
) -> EncodeOptions:
    parsed = parse_analysis_profile_name(name if name is not None else options.analysis_profile)
    return replace(
        options,
        analysis_profile=parsed,
        analysis_settings=resolve_analysis_settings(parsed, stored_profiles),
    )
