# Phase 2: Settings & Configuration UI

## Goals
- Deliver a browser-based setup wizard and settings dashboard that covers credential placement (upload helper or “I already have the file” path entry), sheet ID configuration, and embedding model selection without touching YAML/env vars.
- Enforce validation rules before persisting configuration metadata and keep sensitive files on disk with proper permissions, only storing path/checksum/validated_at in SQLite.
- Provide visibility into current configuration state (last validation, errors, required follow-up actions).

## Success Criteria
- Visiting `/settings` shows credential, sheet, model, audit, and backup cards populated from SQLite plus a diagnostics panel summarizing validation state.
- Credential helper supports two paths: (1) uploading the JSON so the server saves it into the managed directory (`~/.config/chl/credentials/`) with `0o600` permissions, and (2) pointing at an existing on-disk path and validating it without uploading bytes. Both flows update metadata only after validation passes and never duplicate secrets into SQLite.
- Sheet ID and model changes trigger immediate validation (lightweight structural checks today, deeper API probes later) and surface inline errors via htmx swaps.
- Diagnostics panel exposes “Test connectivity” and refreshes credentials/sheets/model badges inline without reloading the entire page.
- Download/restore card provides a JSON snapshot (metadata only) plus a restore textarea that replays credentials, sheets, and model metadata; all actions emit audit log entries for troubleshooting.

## Prerequisites
- Phase 0 settings endpoints and validation services.
- Static asset pipeline in place (Jinja2 templates, CSS framework, htmx library available to templates).
- Managed credential directory configured and writable by the API process.

## Implementation Guide

### 1. UI Structure
- `src/web/templates/settings.html` now composes reusable partials:
  - Header explaining the localhost binding + “Download backup” link.
  - Diagnostics card (`partials/diagnostics_panel.html`) that lists credentials/sheets/models status rows and includes a “Test connectivity” button.
  - Card stack for credentials, sheets, and model preferences. Each card renders its forms plus status pill and metadata grid, and every form uses `hx-post`/`hx-target` to refresh only that card.
  - Audit log card fed by `GET /ui/settings/audit-log` via htmx `hx-trigger="load, settings-changed"` for automatic refresh.
  - Backup card for download + restore flows.
- All cards share the same styling (PicoCSS + custom dark theme) and rely on out-of-band flash updates so inline success/error messages stay in sync.

### 2. Backend Endpoints
- `src/api/routers/ui.py` centralizes rendering helpers (`_build_context`, `_respond`) so every endpoint can emit full-page or partial responses with optional `HX-Trigger` headers.
- Credential, sheet, and model forms call the underlying `SettingsService` methods directly and return the relevant card partial; success responses emit the `settings-changed` htmx trigger so diagnostics/audit cards auto-refresh.
- Diagnostics: `GET /ui/settings/diagnostics` returns the status card; `POST /ui/settings/diagnostics` revalidates the stored credential path (if present), updates timestamps, and returns the same partial + flash message.
- Audit log: `GET /ui/settings/audit-log` streams the latest entries (JSON context pretty-printed) for the UI to poll when `settings-changed` fires.
- Backup restore: `POST /ui/settings/backup/restore` accepts pasted JSON, validates sections individually, replays updates through `SettingsService`, and reuses the card partial for inline feedback.

### 3. Validation Feedback
- `SettingsService.diagnostics()` now returns structured status dataclasses for credentials, sheets, and models (state/headline/detail/timestamp). Credentials validation re-parses the JSON, ensures the file exists, and flags permissive file modes; sheets/models perform structural checks and inherit credential warnings.
- Diagnostics card renders these statuses with colored dots/badges and timestamps; the same data powers each card’s status pill so page and inline updates stay consistent.
- The “Test Connectivity” button re-runs credential validation and swaps only the diagnostics card while also refreshing flash/audit info via htmx triggers.

### 4. Configuration Backup & Restore
- “Download Backup JSON” links to `/ui/settings/backup` which returns the snapshot (credentials path/checksum, sheets tabs, model prefs) as an attachment.
- Restore form accepts pasted JSON, validates each section, and applies updates via the existing services ensuring secrets remain on disk. Successful restores refresh diagnostics/audit cards via `settings-changed` events; failures surface inline errors without mutating state.

### 5. Security & File Handling
- Enforce that uploaded credential files land inside the managed directory (e.g., `~/.config/chl/credentials/`) and reject attempts to escape with `..` or symlinks.
- Path-only submissions must live inside the managed directory or another explicit allow-list; validation should confirm the file exists, is readable, and has `0o600` permissions before accepting it.
- Immediately delete temporary upload after writing final file; never log or echo secret contents.
- Display guidance text reminding users not to expose the API server beyond localhost without additional auth (ties back to deployment assumptions) and clarify that only metadata goes into SQLite.

### 6. UX Polish
- htmx indicators show “Uploading…/Saving…/Restoring…” next to each form while requests are in flight; status pills provide instant feedback without reloading the page.
- Out-of-band flash updates keep the global message banner accurate even when only a single card refreshes; `HX-Trigger` plus polling cards avoid duplicate markup.
- Helper text reinforces the “secrets stay on disk” rule and reminds operators to keep the server behind localhost unless they add their own auth proxy.

## Testing & Validation
- Manual smoke test: start from a blank DB, upload credentials, configure sheets/models, trigger diagnostics, download + restore backup, and verify audit log entries.
- Automated coverage: API tests exercise credential upload/path flows, diagnostics GET/POST, audit-log polling, and backup restore (including error cases for invalid JSON or missing sections).
- Future work: add contract tests for true Google Sheets connectivity once mocked credentials are available.

## Risks & Mitigations
- **User uploads wrong file** → keep previous valid credential until new one passes validation; show side-by-side metadata before switching.
- **Partial configuration state** → highlight missing sections prominently and block downstream operations until resolved.
- **Complex validation delays** → run long validations asynchronously and poll for result; display spinner + “You can navigate away safely” message.
