# Iris Caption data compatibility and recovery

Product releases and each data format evolve independently. No Iris runtime import is used. The data root is `~/Library/Application Support/Iris Caption` on macOS, or `~/.local/share/iris-caption` elsewhere. Below, `data/` means this application data root, not the Iris repository's data directory.

| Data | Owner | Current format and behavior |
| --- | --- | --- |
| `data/settings.json` | `settings.py` | Flat object, independent integer `schema_version: 1`; legacy unversioned object is version 0. Existing settings and unknown keys survive migration and updates. New input is allowlisted; credentials remain in Keychain. |
| `data/view_preferences.json` | `settings.py` | Flat object, independent `schema_version: 1`; legacy version 0 accepted. Unknown stored fields survive, but only validated view choices are exposed to the UI or accepted as new input. Invalid individual view choices are ignored as in the old release. |
| `data/rules/local_rules.json` | `knowledge.py: RuleStore` | Independent `schema_version: 1`, with a `rules` string map. Legacy version 0 was the entire string map. Literal legacy rule names `schema_version` and `rules` are preserved. Unknown envelope metadata survives updates. |
| `data/statistics/statistics.json` | `statistics.py` | Existing independent `version: 1`; no migration or version bump. Strict validation before recording; unknown stored envelope/row fields survive, new snapshots retain the count-only allowlist. |
| `data/materials/materials.json` | `materials.py` | Existing independent `version: 1`; no migration or version bump. Explicitly retained documents only; unknown fields on retained rows and envelope survive. Save validates existing content before replacing the selection. Remote verification is never persisted. |
| `data/knowledge/active.json`, `versions/<generation>/bundle.json` | `knowledge.py` | Immutable generation, manifest `schema_version: 1`; no rewriting or automatic format upgrade. Stored active bundle schema is validated before read/import activation. |
| `data/models/{asr,vad,embedding}/active.json`, `versions/<generation>/manifest.json` and model files | `models.py` | Immutable generations, existing manifest `schema_version: 1`; integrity-pinned model contents are not migrated. |
| Keychain credentials | `settings.py`, `feishu.py` | OS credential store, outside JSON migrations. OAuth refresh data stays in Keychain. |
| Exported Markdown/SRT, user-selected source documents | `export.py` / materials import | Explicit user-owned output/input, never scanned or migrated. |
| Audio, transcript segments, session rules, semantic vectors and remote access verification | session/audio/materials components | Memory-only, outside migration scope. |

## Migration transaction

On first read or update of a legacy settings, preference, or local-rule file, the stable sidecar lock covers read, schema validation, candidate validation, backup, and atomic replacement. Missing files remain absent until explicitly saved. A supported current file is not rewritten on read. There is no global data version tied to the application release.

Before the first schema-changing replacement, the exact original bytes are atomically written and fsynced under `data/backups/<filename>.schema-0-<sha256>.bak`. The full hash identifies the original content. Retries reuse an identical backup; a mismatched existing backup aborts. A backup failure prevents migration. A failure replacing the source leaves the original or complete new file, never a partial file; the backup is already complete. Concurrent app writers use the same stable lock and reload state inside it.

Malformed JSON, duplicate keys, non-object roots, invalid version values, and versions newer than the reader are rejected, preserving the source. Settings/preferences failures raise the standalone `PersistenceError` (`ValueError` subclass) with a user-facing message. Other existing stores retain their `ValueError` error boundary. Filesystem failures propagate; callers show the failure and must not replace the file with defaults. Unknown current-version extension fields are preserved, but unknown newer versions are not interpreted. No startup migration scans the filesystem or copies model weights.

## Explicit rollback and recovery

1. Quit all Iris Caption instances before recovery. Copy the entire application data directory to another location first, retaining both current data and the original backups.
2. To run a release predating these schemas, restore the original matching backup bytes to the corresponding file: `settings.json` backup → `data/settings.json`; `view_preferences.json` backup → `data/view_preferences.json`; `local_rules.json` backup → `data/rules/local_rules.json`. Copy rather than move the backup. If multiple backups exist, select the original snapshot appropriate to that installation; compare contents before restoring.
3. Restoring a pre-migration snapshot discards changes made after that snapshot in the restored file. Preserve the current copy and manually reconcile intended new settings/rules if needed. In particular, the old release expects local rules as a bare string map and cannot read the new envelope. Flat settings/preferences remain readable by the previous release, but a backup restore is the reliable complete rollback path.
4. Do not lower a version marker by hand or start an older release against unsupported data. For genuinely newer files, install a compatible newer app or restore a known compatible snapshot with the app stopped. For corruption without a backup, preserve the damaged original and recover from your external backup; the app does not silently reset it.
5. Model and knowledge generations are immutable and not migrated by this change; no model downloads or deletion are part of recovery. Keychain credentials are unaffected.

All automated migration tests use temporary directories. Development and test runs must not open or mutate the actual user application-data directory.
