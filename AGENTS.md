# Video Compressor repository guide

## Purpose and map

This repository builds the Video Compressor desktop application. `core/` contains
platform-neutral domain and encoding logic, `cli/` contains command-line
entrypoints, `gui/` contains the PySide6 user interface, and `main.py` selects an
entrypoint and is the composition root. Build and packaging helpers live in
`scripts/`; release resources and installer definitions live in `packaging/`.
For release workflow ownership, artifact contracts, and platform-specific
verification, read `docs/release-map.md` before changing those three areas.

## Dependency policy and invariants

- `core` may import only the Python standard library and other `core` modules.
- `cli` may import the standard library, `cli`, and `core`, but never `gui`.
- `gui` may import the standard library, `gui`, and `core` (including Qt).
- `main.py` is the composition root and may connect the application layers.
- Keep module dependencies acyclic; put shared behavior in the lowest suitable
  layer rather than reaching upward into an entrypoint or UI layer.
- Parallel workers must deep-copy and bind an item to one concrete encoder
  before Smart analysis. Never share a mutable `EncodePlanItem` between workers.
- Analysis receipts are keyed by the source, FFmpeg, bound encoder, measurement
  settings, and sample scheme. Quality and size policy changes may reuse the
  measured candidates; encoder or measurement changes may not.
- Publish an encoded file only through its validated temporary path. A Smart
  size miss is a user-visible `NEEDS_DECISION` result, not success or a silent
  skip, and its preserved file must not overwrite the requested output.

## Canonical commands

Install development checks with `python -m pip install -r requirements-dev.txt`.
Run the full validation set with:

```text
ruff check .
pyright
python -m unittest discover -s test -p "test_*.py" -v
```

Run architecture checks alone with
`python -m unittest discover -s test -p "test_architecture.py" -v`.
