# Video Compressor

This project refactors the original single-file compressor into a modular layout with:

- a thin `main.py`
- reusable core planning and execution layers
- preset save/load support
- manual preview sampling before full encode
- VMAF-guided smart compression with final-size enforcement
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
python main.py --cli preview workdir/test.mp4 --backend cpu
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

### Smart compression

New jobs and the built-in HEVC/AV1 presets use smart compression. The default
policy requires VMAF 95 and limits the final file to 70% of the source for
HEVC or 50% for AV1. Smart preview runs the same automatic sample search
without encoding the full video:

```bash
python main.py --cli preview input.mp4 \
  --compression-mode smart \
  --codec hevc \
  --min-vmaf 95 \
  --max-output-ratio 0.70
```

Smart mode requires an FFmpeg build whose `libvmaf` filter and required model
can actually run. The Balance profile analyzes the full video up to 10 seconds
and otherwise uses three 5-second windows; Fast uses shorter samples and fewer
candidates, while Precise uses longer samples and a tighter search. A one-window
custom profile samples the middle of the video.

VMAF is measured at the source's native resolution. The reported value is the
lowest mean VMAF among the sampled windows, not the lowest individual-frame
score, so thresholds are most meaningful when comparing sources at the same
resolution. The built-in threshold remains 95 for compatibility.

If quality and final-size constraints cannot both be met, the configured Smart
policy is applied. The default size-blocked policy relaxes the size limit, and
the default quality-unreachable policy skips the file; `ask` leaves the item in
a **Needs decision** state. CLI exit code `3` means a decision is required,
exit code `2` means analysis or encoding failed, and intentional skips remain a
successful batch outcome.
Candidate sizes are estimated from the largest measured encoded sample
bitrate (including the existing container safety factor) plus the audio
budget; the requested video bitrate is not treated as an observed size. Full
smart encodes are written to a temporary file beside the target and are
published only after the actual size passes validation. A complete encode that
misses the limit is preserved beside the target as `*.size-miss-<id>.*` for an
explicit accept, corrected-bitrate retry, or delete decision.
A corrected-bitrate retry invalidates the old Smart selection and re-runs the
search under the lower video-bitrate ceiling before encoding again.

Smart candidate measurements are cached as versioned JSON receipts under
`workdir/analysis/receipts/`. Changing only VMAF, size, audio, or bitrate policy
re-evaluates those measurements locally; changing the source, FFmpeg binary,
encoder, measurement settings, or sample scheme creates a different receipt.

Use the legacy fixed bitrate policy explicitly when VMAF is unavailable:

```bash
python main.py --cli encode input.mp4 \
  --compression-mode fixed_bitrate \
  --ratio 0.76
```

Presets created by older versions do not opt into smart mode automatically;
they continue to load as fixed bitrate presets.

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

- a project-specific light video-file application icon
- explicit source file and source directory pickers
- editable output, workdir, ffmpeg, and ffprobe paths
- preset load/save/delete controls
- plan summary, preview summary, and encode result summary panels
- a detailed plan/result table with resolution, duration, bitrate, note, and status columns
- smart-analysis stages, selected bitrate, lowest sampled-window mean VMAF, and predicted size
- language switching across English, Simplified Chinese, and any user-provided language packs

### Translations / language packs

- Built-in language packs live in the read-only `config/i18n/` directory and are shipped
  with the app. English is the complete baseline; every built-in pack covers the same keys.
- Explicit user language packs go in the writable runtime `translations/` directory. In a
  source checkout that is `<repo>/translations/`; in the macOS app it is
  `~/Library/Application Support/Video Compressor/translations/`. The file name stem is
  the locale, e.g. `de.json` for German.
- A pack is a flat JSON object of translation keys and must include a `language.name`
  value, which is the display name shown in Settings. Unknown keys, non-string values,
  and entries whose placeholders do not match English are skipped individually; a
  corrupt file or one without a valid `language.name` is skipped entirely. Skipped
  entries are reported as startup diagnostics (CLI: stderr; GUI: Activity Log).
- Overrides are partial: keys you do not provide fall back to the English baseline, so
  a new language only needs the keys you actually translate.
- The CLI `--lang` accepts any discovered locale, and the GUI Settings language list is
  populated from the same catalog.
- Files left behind in the writable `config/i18n/` by older versions are preserved but
  no longer used.

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

Generate or verify the platform icon assets from the canonical SVG:

```bash
python scripts/build_icons.py
python scripts/build_icons.py --check
```

Release builds prepare a pinned native FFmpeg 8.1.2 pair under the ignored
project `workdir/` and require it during packaging:

```bash
python scripts/prepare_ffmpeg.py \
  --target macos-arm64 \
  --output workdir/ffmpeg/macos-arm64

python scripts/build_nuitka.py \
  --clean \
  --version 1.2.3 \
  --ffmpeg-dir workdir/ffmpeg/macos-arm64 \
  --require-ffmpeg
```

The FFmpeg manifest pins URLs and SHA-256 values for Windows, Linux, and
macOS on x86-64 and ARM64. Prepared bundles include FFmpeg, FFprobe, GPLv3
license text, and exact source/build provenance. Downloads, extraction,
signing material, and project-owned temporary files stay under `workdir/`.

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
packaging must use Python 3.12 or older. CI and Release use Python 3.13, so
Windows x86-64 packaging selects MSVC instead of MinGW64.

Windows ARM64 packaging is native and uses the LLVM/Clang backend:

```powershell
python scripts/build_nuitka.py `
  --clean `
  --windows-compiler clang
```

The wrapper's `auto` compiler choice selects Clang on ARM64, MSVC on Python
3.13+ x86-64, and MinGW64 on older x86-64 interpreters. It never
cross-compiles a Windows ARM64 package from an x86 runner.

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

The DMG presents `Video Compressor.app` beside an `Applications` shortcut so
the app can be installed with the standard drag-to-Applications gesture.

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

The package includes `config/`, the runtime SVG icon, `README.md`, and `LICENSE`.
`workdir/` is created at runtime and is not bundled. Local builds bundle a
complete compatible pair from `--ffmpeg-dir` or `FFmpeg/` when available.
Tagged releases require and verify the pinned native FFmpeg/FFprobe pair.

Windows tagged releases also produce a per-user `Setup.exe` (Inno Setup). It
installs without administrator privileges, adds a Start menu shortcut, includes
the same FFmpeg bundle as the portable ZIP, and migrates a legacy v1.6.0 MSI
installation through Windows Installer before installing the new version.
Optional Authenticode signing uses the paired `WINDOWS_CERTIFICATE_BASE64` and
`WINDOWS_CERTIFICATE_PASSWORD` repository secrets; when neither is configured,
the Windows executable and Setup.exe are published unsigned.

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

Each tagged release publishes exactly ten platform packages:

```text
video-compressor-v1.2.3-windows-x86_64.zip
video-compressor-v1.2.3-windows-x86_64-setup.exe
video-compressor-v1.2.3-windows-arm64.zip
video-compressor-v1.2.3-windows-arm64-setup.exe
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
executable relying on emulation. Every tagged package includes FFmpeg 8.1.2
and FFprobe for its exact operating system and CPU architecture.

## License

Video Compressor is released under the [MIT License](LICENSE). Bundled FFmpeg
artifacts keep their own license and source/build provenance alongside the
binary distribution.
