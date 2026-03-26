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

Quick launch from the repo root:

```bash
./launch.sh
```

```bat
launch.bat
```

On Windows, `launch.bat` now prefers the active Conda environment's
`python.exe` when started from an activated PowerShell or Conda shell.

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

## Packaging

Build with PyInstaller from the repo root:

```bash
python scripts/build_pyinstaller.py --clean
```

Common variants:

```bash
python scripts/build_pyinstaller.py --clean --windowed
python scripts/build_pyinstaller.py --clean --onefile
python scripts/build_pyinstaller.py --clean --name video-compressor-gui
python scripts/build_pyinstaller.py --clean --icon packaging/assets/app.ico
```

Convenience launchers:

```bat
scripts\build_windows.bat
```

```bash
./scripts/build_linux.sh
```

Notes:

- The default build uses the spec file at `packaging/video_compressor.spec`.
- `config/` is bundled automatically.
- In frozen builds, the app uses a writable runtime layout next to the executable, so `config/` and `workdir/` remain editable after packaging.
- Optional icons are auto-detected from `packaging/assets/`.
- Windows version metadata is loaded from `packaging/windows_version_info.txt`.
