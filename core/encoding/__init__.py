"""Public planning, analysis, encoding, preview, and parallel-job contracts."""

from core.encoding.analysis import (
    analyze_plan_item,
    item_needs_smart_analysis,
    run_analysis_phase,
)
from core.encoding.executor import execute_plan, execute_plan_item
from core.encoding.parallel import (
    execute_plan_parallel,
    normalize_parallel_backends,
    validate_parallel_options,
)
from core.encoding.planning import build_encode_plan
from core.encoding.preview import execute_preview, execute_smart_preview

__all__ = [
    "analyze_plan_item",
    "build_encode_plan",
    "execute_plan",
    "execute_plan_item",
    "execute_plan_parallel",
    "execute_preview",
    "execute_smart_preview",
    "item_needs_smart_analysis",
    "normalize_parallel_backends",
    "run_analysis_phase",
    "validate_parallel_options",
]
