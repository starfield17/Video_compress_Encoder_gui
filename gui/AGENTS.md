# Video Compressor GUI layer guide

`gui/` is the PySide6 UI layer. It may import `core` and the standard library, and
other `gui` modules — never `cli`, and nothing above it in the dependency graph
(`main.py` is the composition root).

## Entrypoint and composition

- `gui.gui_entry` is the only GUI entrypoint and the only module allowed to import
  `gui.gui_mainwindow`. It builds the `TranslationCatalog`, parses `--lang` against
  it, and constructs `MainWindow`.
- `MainWindow` is the composition root for the GUI: it owns source/output selection,
  presets, workers, the queue, Settings, and the Activity Log, and coordinates the
  sub-panels.
- No other `gui` sub-module may import `gui.gui_mainwindow`.

## EncodeOptionsPanel

`gui.encode_options_panel.EncodeOptionsPanel(QWidget)` owns the Basic / Video /
Audio-Subtitles / Preview / Advanced tabs and all internal wiring: codec/backend
filtering from runtime capabilities, encoder-preset refresh, analysis-profile state,
and Smart/Fixed control syncing.

- `MainWindow` interacts only through the public contract:
  `read_options()`, `read_preview_options()`, `apply_options()`,
  `apply_analysis_profile_settings()`, `current_analysis_profile_name()`,
  `sync_dependent_controls()`, `validate_parallel_options()`,
  `set_runtime_capabilities()`, `notify_capability_detection_failed()`,
  `begin_capability_detection()`, `set_translator()`, `set_busy()`.
- Semantic signals: `codec_changed`, `compression_mode_changed`,
  `analysis_profile_changed`, `options_changed`.
- `MainWindow` must not reach into the panel's raw widgets; tests that need widget
  state access them via `window.options_panel.<widget>`.
- The panel renders the capability snapshot produced by `CapabilityWorker`.
  Encoder entries include `preset_choices`; never run FFmpeg discovery or encoder
  probing synchronously from a widget refresh path.

## Queue layering

- `gui.queue_state` — Qt-free queue record/status/metrics logic. Imports `core` only.
- `gui.queue_actions` — Qt-free quality/size decision and file-side-effect actions
  over one queue record.
- `gui.queue_model` — `QueueTableModel`, `QueueColumn` and column metadata, cell
  formatting, roles and Qt notifications. Delegates mutations to `queue_state` and
  `queue_actions`; never owns receipt, constraint or file side effects.
- `gui.queue_view` — `ResponsiveQueueTableView`, header resize modes, reflow, and the
  `create_queue_view()` factory. May use `gui.queue_model`'s column definitions.
- `gui.queue_manager` — Qt worker/thread orchestration over the model.
- View may depend on the model; model and state must not depend on the view.

## MainWindow scope (evaluated 2026-08-21)

`MainWindow` remains the GUI composition root. Its UI construction is split into
same-class builder methods and preview summary formatting lives in
`preview_result_dialog`; avoid introducing pass-through controller objects. Revisit a
`SourcePanel` / `QueueDashboard` extraction only when a cohesive feature has a narrow
interface. Do not split by line count alone.

## Canonical checks

```text
ruff check .
pyright
python -m unittest discover -s test -p "test_architecture.py" -v
python -m unittest discover -s test -p "test_*.py" -v
```
