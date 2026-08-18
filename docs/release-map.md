# Release map

This map covers the release vertical slice. Keep repository-wide architecture
rules in `AGENTS.md`; keep platform implementation details near their scripts.

## Entry points and ownership

| Path | Responsibility |
| --- | --- |
| `.github/workflows/ci.yml` | Runs checks, a fast pinned-ISCC Windows installer contract, and six native packaging smoke jobs on pushes and pull requests. |
| `.github/workflows/release.yml` | Gates numeric `v*` tags through the pinned-ISCC installer contract, builds the same six targets, verifies ten final packages, writes checksums, and creates or updates the GitHub Release. |
| `scripts/build_nuitka.py` | Owns Nuitka command construction, version normalization, staging, and platform output discovery. |
| `scripts/prepare_ffmpeg.py` | Downloads and validates the target-specific FFmpeg bundle described by `packaging/ffmpeg/manifest.json`. |
| `scripts/build_setup.py` | Validates the staged Windows tree and passes release, path, and architecture defines to Inno Setup. |
| `packaging/windows/installer.iss` | Owns Windows installer identity and behavior, including migration from legacy MSI installs. |
| `scripts/sign_windows.ps1` | Authenticode-signs the staged application executable and final Setup executable when release secrets exist. |
| `test/test_release_workflows.py` | Enforces matrix targets and cross-workflow artifact contracts. |
| `test/test_windows_setup.py` | Enforces the Python-to-Inno command contract and installer manifest invariants. |

## Contracts

- CI and Release must keep the same six target names and native runners. Change
  both workflows and `test/test_release_workflows.py` together.
- The release matrix produces ten packages: two Windows ZIPs, two Windows Setup
  executables, two Linux tarballs, two macOS tarballs, and two macOS DMGs.
- `packaging/ffmpeg/manifest.json` is the source of truth for FFmpeg URLs and
  checksums. Every release target must have one matching manifest entry.
- `scripts/build_nuitka.py` is the source of truth for accepted numeric versions
  and generated package layout. Release tags use `vMAJOR.MINOR.PATCH` or the
  supported four-part numeric form.
- `packaging/windows/installer.iss` is the single source of truth for the Inno
  `AppId`. A literal GUID begins with `{{` because Inno escapes a literal left
  brace by doubling it. Do not inject the AppId with an ISCC `/D` define.
- The legacy MSI `WindowsInstaller` registry value is `REG_DWORD`; query it with
  `RegQueryDWordValue`, not `RegQueryStringValue`. Registry helper root keys use
  Inno's `HKEY` type and built-in `HKCU`/`HKLM` constants; do not redeclare them.
- CI and Release both install Inno Setup 7.1.0 in portable mode under `workdir`,
  pass its exact `ISCC.exe` path to `build_setup.py`, and compile `installer.iss`
  for x86_64 and ARM64 before any expensive Nuitka release packaging starts.
- A pushed release tag is immutable input to the workflow. Rerunning a failed
  workflow checks out the commit still referenced by the remote tag, not a newer
  commit on `main` or a locally moved tag.

## Verification

Run the local contract checks with the repository's Python environment:

```text
ruff check .
pyright
python -m unittest discover -s test -p "test_*.py" -v
```

Windows CI first compiles the Inno manifest for x86_64 and ARM64 with a minimal
dummy payload using the explicitly selected portable ISCC 7.1.0. This fast
contract is the executable check
for `.iss` syntax when ISCC is unavailable on the development host. The later
Windows packaging smoke jobs still compile the real Nuitka payload, silently
install it, inspect it, and silently uninstall it as the end-to-end check.

Before tagging a release, confirm the target commit's main CI run succeeded.
After pushing the annotated tag, confirm all six build jobs, the publish job,
the ten release packages, and `SHA256SUMS.txt`.
