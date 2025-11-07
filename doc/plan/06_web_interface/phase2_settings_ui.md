# Phase 2: Settings & Configuration UI

## Goals
- Deliver a browser-based setup wizard and settings dashboard that covers credential placement (upload helper or “I already have the file” path entry), sheet ID configuration, and embedding model selection without touching YAML/env vars.
- Enforce validation rules before persisting configuration metadata and keep sensitive files on disk with proper permissions, only storing path/checksum/validated_at in SQLite.
- Provide visibility into current configuration state (last validation, errors, required follow-up actions).

## Success Criteria
- Visiting `/settings` displays sections for credentials, sheet IDs, embedding model, and advanced options with current values populated from SQLite.
- Credential helper supports two paths: (1) uploading the JSON so the server saves it into the managed directory (`~/.config/chl/credentials/`) with `0o600` permissions, and (2) pointing at an existing on-disk path and validating it without uploading bytes. Both flows update metadata only after validation passes and never duplicate secrets into SQLite.
- Sheet ID and model changes trigger immediate validation (call Sheets API, check model availability) and surface inline errors.
- Settings page exposes “Rotate credentials”, “Test connectivity”, and “Download configuration backup” actions.
- All changes emit audit log entries (who/when/what) stored locally for troubleshooting.

## Prerequisites
- Phase 0 settings endpoints and validation services.
- Static asset pipeline in place (Jinja2 templates, CSS framework, htmx library available to templates).
- Managed credential directory configured and writable by the API process.

## Implementation Guide

### 1. UI Structure
- Add `src/web/templates/settings.html` with sections:
  1. **Setup Wizard** (if required configs missing) guiding first-time users.
  2. **Credentials** card showing current status (valid/invalid, last checked timestamp, checksum snippet) plus two tabs: “Upload to this machine” (default) and “Use existing file” (advanced path input with helper text reminding users to place the file somewhere the server can read).
  3. **Sheets** form for review + published IDs (two inputs plus validate button).
  4. **Embedding Model** selector with radio buttons (e.g., small/medium/large) and disk space indicator.
  5. **Advanced** collapsible area for managed paths, telemetry toggles.
- Use htmx forms to submit each section independently, returning partials for inline updates.

### 2. Backend Endpoints
- In `src/api/routers/ui.py`, add handlers for rendering templates and returning partial fragments.
- Map forms to Phase 0 REST endpoints using server-side fetches (or call services directly to avoid double serialization when rendering).
- Provide `POST /ui/settings/credentials/upload` that accepts `UploadFile`, writes to managed directory, triggers validation, and returns success/error partial.
- Provide `POST /ui/settings/credentials/path` that accepts a server-side path string, checks that it resides within the managed root (or an allow-listed location), validates the credential, and simply stores metadata pointing to that path (no bytes copied).

### 3. Validation Feedback
- Extend settings service to surface structured validation results (status enum, message, remediation).
- Render badges/icons (e.g., green “Valid”, orange “Needs attention”) next to each section.
- Offer “Test Connectivity” button that runs the same validation pipeline and streams the result (htmx swap).

### 4. Configuration Backup & Restore
- Add action to download current metadata + pointers as JSON for support purposes (no secret blobs).
- Optionally support uploading a metadata backup to restore previous sheet IDs/model choices.

### 5. Security & File Handling
- Enforce that uploaded credential files land inside the managed directory (e.g., `~/.config/chl/credentials/`) and reject attempts to escape with `..` or symlinks.
- Path-only submissions must live inside the managed directory or another explicit allow-list; validation should confirm the file exists, is readable, and has `0o600` permissions before accepting it.
- Immediately delete temporary upload after writing final file; never log or echo secret contents.
- Display guidance text reminding users not to expose the API server beyond localhost without additional auth (ties back to deployment assumptions) and clarify that only metadata goes into SQLite.

### 6. UX Polish
- Include progress indicator when validating credentials (htmx `hx-indicator`).
- Auto-disable submit buttons while validation in flight to avoid duplicate requests.
- Provide contextual help links to docs for obtaining sheet IDs or generating Google service accounts.

## Testing & Validation
- Browser-based manual test: run through setup wizard from blank database to fully configured state.
- Automated tests for upload endpoint (permission bits, checksum calculation, failure cases such as invalid JSON).
- Integration test ensuring invalid sheet IDs produce inline errors and do not persist changes.
- Accessibility pass (keyboard navigation, focus management) for key forms.

## Risks & Mitigations
- **User uploads wrong file** → keep previous valid credential until new one passes validation; show side-by-side metadata before switching.
- **Partial configuration state** → highlight missing sections prominently and block downstream operations until resolved.
- **Complex validation delays** → run long validations asynchronously and poll for result; display spinner + “You can navigate away safely” message.
