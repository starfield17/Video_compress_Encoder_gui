# Video Compressor

This project refactors the original single-file compressor into a modular layout with:

- a thin `main.py`
- reusable core planning and execution layers
- preset save/load support
- manual preview sampling before full encode
- CLI and PySide6 GUI entrypoints
- configurable English and Simplified Chinese language packs
- optional copy of matching external subtitle sidecars such as `.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`, `.idx`, and `.sup`

## Layout

```text
main.py
core/
cli/
gui/
config/
workdir/
```

## CLI examples

```bash
python main.py --cli plan workdir/test.mp4
python main.py --cli preview workdir/test.mp4 --backend cpu --sample-duration 5
python main.py --cli encode workdir/test.mp4 --backend cpu --overwrite
python main.py --cli encode workdir/test.mp4 --copy-external-subtitles
python main.py --cli preset list
```

Language can be selected with `--lang en` or `--lang zh_cn`.

## GUI

Run the GUI with:

```bash
python main.py
```

Or explicitly:

```bash
python main.py --gui --lang zh_cn
```

The GUI now includes:

- explicit source file and source directory pickers
- editable output, workdir, ffmpeg, and ffprobe paths
- preset load/save/delete controls
- plan summary, preview summary, and encode result summary panels
- a detailed plan/result table with resolution, duration, bitrate, note, and status columns
- English and Simplified Chinese language switching

## Notes

- `ffmpeg` and `ffprobe` must be available on `PATH`, unless explicit paths are set in the CLI or GUI.
- Presets are stored in `config/presets/`.
- Preview outputs, logs, and temp files are written into `workdir/`.
- The GUI is PySide6-only.
