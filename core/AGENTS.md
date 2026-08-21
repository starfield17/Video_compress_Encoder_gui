# Core module guide

## Smart analysis boundaries

- `content_complexity.py` owns FFmpeg Scout command construction and strict
  SI/TI/scene metadata parsing. It must not know about sample selection or
  bitrate search.
- `sample_planner.py` is the deterministic, subprocess-free sampling domain.
  It may depend on `core.models` but not on FFmpeg or Smart orchestration.
- `smart_sampling.py` is the adapter that executes Scout and scene-guard
  commands through its supplied runner, then returns a `SamplingResult`.
- `smart_bitrate.py` owns bitrate budgets, candidate search and reselection.
- `smart_cache.py` owns Smart measurement/quality fingerprints and receipt
  construction. Measurement identity must not include quality/size policy.
- `smart_measurement.py` owns FFmpeg/VMAF command execution and scoring one
  candidate; it does not choose candidates or queue decisions.
- `smart_workflow.py` owns the top-level Smart lifecycle: reuse, planning,
  coarse search, holdout promotion, refinement and receipt persistence.
- `smart_quality.py` is the stable compatibility facade. New core callers should
  import the focused owner unless they need the top-level `analyze_quality()` API.
- `constraint_resolution.py` owns user-decision policy and preserved-output
  actions. Smart workflow must not import it.

Keep the dependency direction:

```text
content_complexity ─┐
                    ├─> smart_sampling ─┐
sample_planner ─────┘                   │
smart_bitrate ─────────────────────────┼─> smart_workflow -> smart_quality facade
smart_cache ───────────────────────────┤
smart_measurement ─────────────────────┘

smart_bitrate + smart_cache -> constraint_resolution
```

The architecture test enforces these dependency directions.
Run targeted checks with:

```text
python -m unittest discover -s test -p "test_content_complexity.py" -v
python -m unittest discover -s test -p "test_sample_planner.py" -v
python -m unittest discover -s test -p "test_architecture.py" -v
```
