# Video Compressor

This project refactors the original single-file compressor into a modular layout with:

- a thin `main.py`
- reusable core planning and execution layers
- preset save/load support
- manual preview sampling before full encode
- CLI and PySide GUI entrypoints
- configurable English and Simplified Chinese language packs

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

## Notes

- `ffmpeg` and `ffprobe` must be available on `PATH`, unless explicit paths are passed in the CLI.
- Presets are stored in `config/presets/`.
- Preview outputs, logs, and temp files are written into `workdir/`.
