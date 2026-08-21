# Smart capability guide

`core.smart` owns VMAF-guided analysis, sampling, measurement identity and
constraint decisions. CLI and GUI use the package API; other core packages
import the concrete owner module.

## Internal boundaries

- `sampling.complexity` builds and parses Scout metadata only.
- `sampling.planner` is deterministic and subprocess-free.
- `sampling.scout` executes Scout and scene-alignment commands.
- `bitrate` owns budgets, candidate search and reselection.
- `cache` owns measurement/quality fingerprints and receipt construction.
- `measurement` owns FFmpeg/VMAF execution for one candidate.
- `workflow` owns reuse, planning, coarse search, holdout promotion,
  refinement and receipt persistence; it never imports `decisions`.
- `decisions` owns user choice policy and preserved size-miss actions.

Measurement identity includes the source, FFmpeg, bound encoder, measurement
settings and sample scheme. Quality and size policy changes may reuse measured
candidates; encoder or measurement changes may not.

Run Smart checks with:

```text
python -m unittest discover -s test -p "test_smart_quality.py" -v
python -m unittest discover -s test -p "test_analysis_runtime.py" -v
python -m unittest discover -s test -p "test_constraint_decisions.py" -v
python -m unittest discover -s test -p "test_architecture.py" -v
```
