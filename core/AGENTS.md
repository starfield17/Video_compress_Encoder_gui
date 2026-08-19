# Core module guide

## Smart analysis boundaries

- `content_complexity.py` owns FFmpeg Scout command construction and strict
  SI/TI/scene metadata parsing. It must not know about sample selection or
  bitrate search.
- `sample_planner.py` is the deterministic, subprocess-free sampling domain.
  It may depend on `core.models` but not on FFmpeg or Smart orchestration.
- `smart_sampling.py` is the adapter that executes Scout and scene-guard
  commands through its supplied runner, then returns a `SamplingResult`.
- `smart_quality.py` owns bitrate search, VMAF measurement, holdout promotion,
  refinement, and top-level analysis lifecycle. It consumes the three modules
  above through their public functions and data classes.

Keep the dependency direction:

```text
content_complexity ─┐
                    ├─> smart_sampling -> smart_quality -> vmaf_runtime
sample_planner ─────┘
```

The architecture test enforces the lower three modules' direct core imports.
Run targeted checks with:

```text
python -m unittest discover -s test -p "test_content_complexity.py" -v
python -m unittest discover -s test -p "test_sample_planner.py" -v
python -m unittest discover -s test -p "test_architecture.py" -v
```
