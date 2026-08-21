# Encoding capability guide

`core.encoding` owns the transition from an immutable plan to analysis and
encoding results. CLI and GUI callers use the package API; implementation
modules import concrete owners in `config`, `media`, `ffmpeg`, and `smart`.

## Internal boundaries

- `planning` discovers inputs, resolves one encoder, probes media, and builds
  validated plan items.
- `analysis` runs Smart analysis and converts policy outcomes into terminal
  item results or encode-ready items.
- `process` owns FFmpeg process lifecycle, cancellation, logging, and progress
  parsing.
- `item_results` owns result construction, sidecar publication, failure logs,
  and preserved size-miss paths.
- `executor` performs serial item/plan execution and publishes validated Smart
  output.
- `preview` executes fixed and Smart preview jobs.
- `parallel` deep-copies each plan item, binds it to one concrete encoder, runs
  analysis first, then schedules encode workers.

Never share a mutable `EncodePlanItem` between workers. A Smart output is
written beside its destination under a temporary name and published only after
size validation. A size miss remains a `NEEDS_DECISION` result and its preserved
file must not overwrite the requested output.

Run Encoding checks with:

```text
python -m unittest discover -s test -p "test_parallel_transcoding.py" -v
python -m unittest discover -s test -p "test_quality_tuning.py" -v
python -m unittest discover -s test -p "test_subprocess_utils.py" -v
python -m unittest discover -s test -p "test_architecture.py" -v
```
