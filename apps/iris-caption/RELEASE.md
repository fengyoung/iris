# Standalone Caption releases

Caption has its own product version, beginning at **0.2.0**. The literal `__version__` in `src/iris_caption/version.py` is authoritative. Never increment the main Iris product version for a Caption release.

Run commands from `apps/iris-caption` using the existing development environment. The release script invokes PyInstaller only; it does not install/reinstall dependencies. Native macOS arm64 is required to build. Ensure the environment already contains the app dependencies, PyInstaller, pytest, and Ruff.

```bash
.venv/bin/python scripts/release.py build --dry-run
.venv/bin/python scripts/release.py import-legacy --dry-run
# Once tests and release authorization are complete:
.venv/bin/python scripts/release.py import-legacy
.venv/bin/python scripts/release.py build
```

`build --dry-run` reads the version using AST without importing application code, reports the planned output, and changes nothing. A real build reserves the output with FileLock before allocating its number. PyInstaller work, output and cache all live in the attempt's private staging directory; system-wide caches are not cleaned. It runs the complete app pytest suite and Ruff, builds privately, and runs frozen `--diagnostics` and `--smoke-test` (headless, with a timeout) before publication. Any failed check prevents publication. No installed app or public `dist/声笺.app` is modified.

The default immutable release is:

```text
releases/0.2.0/
  Iris-Caption-0.2.0-macos-arm64.zip
  manifest.json
  validation.json
  CHANGELOG.md
  README.md
  RELEASE.md
  docs/data-compatibility.md
release-state/
  counter.json
  counter.lock
```

The ZIP contains the app bundle, produced with macOS `ditto` to retain permissions and symlinks. The manifest records SHA-256, bytes, validation results, source fingerprint, and build information: `version`, `build_number`, `built_at` (UTC), `source_revision` (a SHA-256 source fingerprint prefix), `channel` (defaults to preview), and `architecture`. Git HEAD is recorded separately as context. Source fingerprints include application code, scripts, tests, assets, launcher, spec and packaging metadata; caches and generated build outputs are excluded. The source must stay unchanged throughout the build.

The counter is locked, atomically replaced, and fsynced under `release-state`, outside disposable `dist`. Every attempt that passes reservation/platform checks consumes one strictly increasing number, including attempts that later fail. Keep this directory backed up; do not delete or reset it. Use the same `--state` when deliberately changing `--output`. Independent machines must share a suitably locking release-state/output location or coordinate their counters externally.

Publication atomically renames a complete private generation using the operating system's exclusive no-replace operation. An existing version, including one retained in `archive`, is rejected. Simultaneous builds serialize under the output lock; they cannot overwrite a release. Change the Caption source version for a new release. `--channel preview` changes metadata only and does not permit a second package for the same version.

The spec requires `IRIS_CAPTION_BUILD_INFO`, supplied by the release script. It verifies the staged version against the source literal and bundles the private JSON as `assets/build-info.json`. `CFBundleShortVersionString` uses the product version; `CFBundleVersion` uses the allocated build number. Direct `pyinstaller IrisCaption.spec` without staged metadata intentionally fails. Source development uses build number 0 and no build timestamp through the application's metadata API.

## Retention and legacy previews

```bash
.venv/bin/python scripts/release.py retention
.venv/bin/python scripts/release.py retention --apply
```

The default command only prints a plan. `--apply` keeps the current and immediately previous completed versions in `releases` and moves older verified releases into `releases/archive` using exclusive publication. It never deletes anything. Version order is numeric. Every managed manifest and ZIP checksum is verified before planning or archiving; unknown directories, symlinks and loose legacy files are untouched. Existing archive destinations are never replaced. This operation is explicit and is not automatically run after a build.

`import-legacy` copies exactly `dist/Iris-Caption-0.1.0-noise-filter-preview.zip`, the last noise-filter preview, into the managed 0.1.0 release directory. The original archive remains unchanged. The manifest identifies the legacy source and leaves unknown build number, timestamp and revision as null; it does not claim the old binary contains new metadata. Import before introducing 0.2.0 so 0.1.0 is the immediately previous release. Older branded/device-width previews are not automatically deleted or moved. Without an import, retention still reports the known noise-filter archive as the legacy previous version while fewer than two managed releases exist.

## Tooling checks without a build

```bash
.venv/bin/python -m pytest tests/test_release.py -q
.venv/bin/python -m ruff check scripts/release.py tests/test_release.py IrisCaption.spec
```

Tests exercise counter concurrency, AST-only version reading, immutable publication, failure consumption, private staging, checksum validation, retention and legacy preservation with fake bundles. No actual PyInstaller build is performed by these tests.
