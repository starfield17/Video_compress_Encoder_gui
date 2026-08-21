"""Public Smart-analysis and size-decision contract.

Core implementation modules import the concrete owner module. CLI and GUI
adapters use this deliberately small package API.
"""

from core.smart.bitrate import (
    SmartBitrateBudget,
    calculate_smart_bitrate_budget,
    parse_bitrate_bps,
    predicted_output_size,
    reselect_from_candidates,
    resolve_max_output_ratio,
    search_bitrate_candidates,
)
from core.smart.cache import (
    SMART_ANALYSIS_ALGORITHM_VERSION,
    SMART_SAMPLE_SCHEME_VERSION,
    measurement_configuration_fingerprint,
    measurement_configuration_payload,
    quality_configuration_fingerprint,
)
from core.smart.concurrency import SMART_ANALYSIS_SEMAPHORE, acquire_analysis_slot
from core.smart.decisions import (
    accept_rejected_output,
    apply_decision_to_options,
    build_decision_options,
    constraint_policy_from_size_blocked,
    discard_rejected_output,
    prepare_size_miss_retry,
    reselect_after_quality_decision,
    size_blocked_from_constraint_policy,
)
from core.smart.measurement import SMART_ERROR_TAIL_CHARS, SampleWindow, SmartCommandError
from core.smart.profiles import (
    analysis_profiles_from_config,
    bind_analysis_profile,
    parse_analysis_profile_name,
)
from core.smart.receipts import delete_analysis_receipt
from core.smart.vmaf import VMAF_PRODUCTION_MODELS, probe_vmaf_runtime
from core.smart.workflow import analyze_quality, choose_smart_sample_windows


__all__ = [
    "SMART_ANALYSIS_ALGORITHM_VERSION",
    "SMART_ANALYSIS_SEMAPHORE",
    "SMART_ERROR_TAIL_CHARS",
    "SMART_SAMPLE_SCHEME_VERSION",
    "SampleWindow",
    "SmartBitrateBudget",
    "SmartCommandError",
    "VMAF_PRODUCTION_MODELS",
    "accept_rejected_output",
    "acquire_analysis_slot",
    "analysis_profiles_from_config",
    "analyze_quality",
    "apply_decision_to_options",
    "bind_analysis_profile",
    "build_decision_options",
    "calculate_smart_bitrate_budget",
    "choose_smart_sample_windows",
    "constraint_policy_from_size_blocked",
    "delete_analysis_receipt",
    "discard_rejected_output",
    "measurement_configuration_fingerprint",
    "measurement_configuration_payload",
    "parse_analysis_profile_name",
    "parse_bitrate_bps",
    "predicted_output_size",
    "prepare_size_miss_retry",
    "probe_vmaf_runtime",
    "quality_configuration_fingerprint",
    "reselect_after_quality_decision",
    "reselect_from_candidates",
    "resolve_max_output_ratio",
    "search_bitrate_candidates",
    "size_blocked_from_constraint_policy",
]
