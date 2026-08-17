from __future__ import annotations

import os


MAX_ANALYSIS_JOBS = 2
MIN_CPUS_FOR_PARALLEL_ANALYSIS = 8


def analysis_concurrency_limit(*, cpu_count: int | None = None) -> int:
    """How many Smart analyses may run at once.

    Analysis is cheaper than a full encode, but each job still runs CPU VMAF.
    Two jobs are enough to overlap I/O and startup; more would oversubscribe
    the VMAF thread budget. Encode never shares this slot.
    """
    count = int(cpu_count) if cpu_count is not None else (os.cpu_count() or 4)
    if count < 1:
        count = 1
    if count >= MIN_CPUS_FOR_PARALLEL_ANALYSIS:
        return MAX_ANALYSIS_JOBS
    return 1
