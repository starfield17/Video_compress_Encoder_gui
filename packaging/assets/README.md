`app.svg` is the canonical project icon. Regenerate the committed platform
assets after editing it:

```bash
python scripts/build_icons.py
```

- `app.ico`: Windows executable and MSI metadata.
- `app.icns`: macOS application bundle icon.
- `app.png`: high-resolution preview and fallback raster.
- `app.svg`: runtime window icon on every platform.

Use `python scripts/build_icons.py --check` in CI to detect stale generated
assets.
