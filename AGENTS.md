# AGENTS.md

## Design practices

- Prefer responsive, optimistic UI behavior.
- Show immediate feedback on user actions; do not wait for long async operations before updating UI state.
- Keep layout geometry stable during state transitions to avoid cursor-jump behavior.
- Keep action controls spatially consistent; toggled states should not move controls unexpectedly.
- Place loading indicators at the locus of work (where users are looking for progress).
- Avoid replacing entire components when in-place state transitions can preserve continuity.
- Support concurrent operations where backend semantics allow it.
- Use progressive disclosure for dense configuration and details.
- Hide irrelevant controls and empty sections; show actions only when they are meaningful for current state.
- Keep interaction patterns keyboard-accessible and predictable.
- Make status/state explicit with clear, user-facing labels.

## Data and behavior principles

- Treat server-side state as authoritative for shared/app-critical data.
- Persist user/app state on the backend so sessions are consistent across devices.
- Reconcile optimistic client state with server state without causing abrupt UI jumps.
- Ensure shutdown/cleanup paths are deterministic and do not require repeated user intervention.
- Do not bundle multiple files into zip/tar archives by default; upload original files individually.
- Only upload archive files when the user explicitly asks for an archive upload.
- If an upload batch partially fails, retry only failed files and never re-upload files that already succeeded.

## Implementation quality

- Favor state-driven rendering over ad hoc DOM behavior.
- Keep async flows cancellation-safe and shutdown-safe.
- Validate changes with appropriate build/test checks before handoff.

## Git safety

- Never modify git history.
- Never use `--force` with git commands.
- Prefer forward-only commits and standard (non-force) pushes.
- Never commit non-operational files (for example: generated media, temporary exports, logs, screenshots, or local debug artifacts). Commit only files required for runtime/build/test behavior (source/config/dependency metadata) unless the user explicitly asks to include non-operational files.
