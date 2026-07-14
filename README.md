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
FFmpeg/
config/
workdir/
```

## CLI examples

```bash
python main.py --cli plan workdir/test.mp4
python main.py --cli preview workdir/test.mp4 --backend cpu --sample-duration 5
python main.py --cli encode workdir/test.mp4 --backend qsv --overwrite
python main.py --cli encode workdir/test.mp4 --backend cpu --overwrite
python main.py --cli encode workdir/test.mp4 --copy-external-subtitles
python main.py --cli preset list
```

Language can be selected with `--lang en` or `--lang zh_cn`.
Supported backend values are `auto`, `cpu`, `nvenc`, `qsv`, and `amf`.
When `auto` is selected, the planner prefers smoke-tested runtime encoders in this order: `nvenc`, `qsv`, `amf`, then `cpu`.

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

- Explicit GUI/CLI `ffmpeg` / `ffprobe` paths take priority; otherwise the app checks the project-root `FFmpeg/` directory before falling back to system-installed tools.
- Supported bundled layouts are `FFmpeg/ffmpeg(.exe)` + `FFmpeg/ffprobe(.exe)` and `FFmpeg/bin/ffmpeg(.exe)` + `FFmpeg/bin/ffprobe(.exe)`.
- Intel QSV requires an FFmpeg build that exposes `hevc_qsv` and/or `av1_qsv`, plus supported Intel graphics hardware/drivers.
- Presets are stored in `config/presets/`.
- Preview outputs, logs, and temp files are written into `workdir/`.
- The GUI is PySide6-only.

## Packaging

Install the build dependencies from the repo root:

```bash
python -m pip install -r requirements-build.txt
```

Build a standalone package locally:

```bash
python scripts/build_nuitka.py --clean
```

Build with a release version:

```bash
python scripts/build_nuitka.py --clean --version 1.2.3
```

On Windows, the default build uses Nuitka-managed MinGW64:

```bash
python scripts/build_nuitka.py --clean
```

Nuitka downloads its supported MinGW64 compiler automatically, so Visual
Studio Build Tools are not required. To use MSVC explicitly:

```powershell
python scripts/build_nuitka.py `
  --clean `
  --windows-compiler msvc
```

MSVC mode requires Visual Studio 2022 C++ Build Tools or later. MinGW64
packaging must use Python 3.12 or older; CI and Release currently use Python
3.12.

Convenience scripts:

```bat
scripts\build_windows.bat
```

```bash
./scripts/build_linux.sh
```

The normalized output is:

```text
dist/video-compressor/
```

Packaging uses Nuitka standalone directory mode. Builds run natively on each
target platform; this is multi-platform release automation, not single-host
cross-compilation.

The package includes `config/` and `README.md`. `workdir/` is created at
runtime and is not bundled. FFmpeg is included only when a complete compatible
`ffmpeg`/`ffprobe` pair exists under `FFmpeg/`; otherwise the application
continues to resolve an installed system FFmpeg.

Tags such as `v1.2.3` trigger four-platform GitHub Releases for Windows
x86-64, Linux x86-64, and macOS Intel and Apple Silicon. macOS output is
currently a standalone directory archive rather than a signed or notarized
`.app`.
