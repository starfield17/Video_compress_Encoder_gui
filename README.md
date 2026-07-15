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
Supported backend values are `auto`, `cpu`, `nvenc`, `qsv`, `amf`, and
`videotoolbox`. When `auto` is selected, the planner prefers smoke-tested
runtime encoders in this order: `nvenc`, `qsv`, `amf`, `videotoolbox`, then
`cpu`.

### macOS VideoToolbox acceleration

On supported macOS FFmpeg builds, the project can use `hevc_videotoolbox` for
HEVC hardware encoding and optionally request hardware decoding with
`-hwaccel videotoolbox`. VideoToolbox support depends on the selected FFmpeg
build. The project performs a real one-frame encoder smoke test, and uses
`-allow_sw 0` so an unavailable hardware encoder cannot silently fall back to
software. Hardware decoding is optional and defaults to software decoding.

This version does not implement zero-copy hardware frames or
`-hwaccel_output_format videotoolbox`, and VideoToolbox does not provide AV1
support in this project.

Parallel VideoToolbox encode/decode workers may contend for shared Apple media
hardware; parallel mode is never enabled automatically.

VideoToolbox CLI examples:

```bash
python main.py --cli encode input.mp4 \
  --codec hevc \
  --backend videotoolbox \
  --overwrite
```

```bash
python main.py --cli encode input.mp4 \
  --codec hevc \
  --backend videotoolbox \
  --decode-acceleration videotoolbox \
  --overwrite
```

Diagnostic commands:

```bash
ffmpeg -hide_banner -encoders | grep videotoolbox
ffmpeg -hide_banner -hwaccels
ffmpeg -hide_banner -h encoder=hevc_videotoolbox
```

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

Windows ARM64 packaging is native and uses the LLVM/Clang backend:

```powershell
python scripts/build_nuitka.py `
  --clean `
  --windows-compiler clang
```

The wrapper's `auto` compiler choice selects Clang on ARM64 and MinGW64 on
x86-64. It never cross-compiles a Windows ARM64 package from an x86 runner.

Build a native macOS application bundle and DMG on the matching Mac:

```bash
python scripts/build_nuitka.py \
  --clean \
  --version 0.2.0 \
  --macos-app-bundle \
  --target-arch arm64
```

The app build uses Nuitka `app-dist` mode and produces:

```text
dist/Video Compressor.app/
dist/video-compressor.dmg
```

The app's read-only resources are under `Contents/Resources`. Configuration,
logs, previews, and temporary files are written to
`~/Library/Application Support/Video Compressor`, outside the app bundle.
The standalone package continues to use the executable-adjacent layout:

```text
dist/video-compressor/
```

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

### Native release matrix

A tag such as `v1.2.3` produces six native builds:

| Target | Runner/package |
| --- | --- |
| Windows x86-64 | Native Windows x86-64 standalone package |
| Windows ARM64 | Native Windows ARM64 standalone package |
| Linux x86-64 | Native Ubuntu x86-64 standalone package |
| Linux ARM64 | Native Ubuntu ARM64 standalone package |
| macOS Intel | Native x86-64 `.app` bundle |
| macOS Apple Silicon | Native arm64 `.app` bundle |

Each tagged release publishes exactly eight platform packages:

```text
video-compressor-v1.2.3-windows-x86_64.zip
video-compressor-v1.2.3-windows-arm64.zip
video-compressor-v1.2.3-linux-x86_64.tar.gz
video-compressor-v1.2.3-linux-arm64.tar.gz
video-compressor-v1.2.3-macos-x86_64.tar.gz
video-compressor-v1.2.3-macos-arm64.tar.gz
video-compressor-v1.2.3-macos-x86_64.dmg
video-compressor-v1.2.3-macos-arm64.dmg
```

The macOS tarballs and DMGs contain native `.app` bundles. Intel and Apple
Silicon builds are separate native packages, not universal binaries merged
with `lipo`. Releases are ad-hoc signed; they are not Developer ID signed or
notarized, so Gatekeeper may require using **Open** or right-clicking the app
and choosing **Open**. Linux ARM64 requires a sufficiently recent glibc
distribution. Windows ARM64 is a native ARM package rather than an x86
executable relying on emulation. FFmpeg, when bundled, must also match the
operating system and CPU architecture.
