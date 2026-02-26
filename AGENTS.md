# AGENTS.md

## 1) Hierarchy

- `SYSTEM_PROMPT.md` is the shared cross-project core prompt.
- This file defines repository-specific rules.
- Use `yarn` (not `npm`) for JavaScript package management.

## 2) Product behavior

- Prefer responsive, optimistic UI with immediate local feedback.
- Keep layout/control positions stable through state transitions.
- Put progress indicators where work is happening.
- Prefer in-place state transitions over full component swaps.
- Use progressive disclosure; hide irrelevant controls/empty sections.
- Keep interactions keyboard-accessible and state labels explicit.
- Support concurrent operations when backend semantics allow it.

## 3) Data and reliability

- Treat server state as authoritative for shared/app-critical data.
- Persist state on backend for cross-session consistency.
- Reconcile optimistic state without abrupt UI jumps.
- Ensure deterministic shutdown/cleanup paths.
- For batched uploads, retry only failed files.
- Do not upload archives unless explicitly requested.

## 4) Quality bar

- Favor state-driven rendering.
- Keep async flows cancellation-safe and shutdown-safe.
- Validate before handoff.
- Time each new test in isolation.
- New individual tests must run in <= 1s.
- Agent-specific changes must not regress other supported agents.

## 5) UI evidence (required for UI-rendering changes)

- Use real app + real backend (no mock backend unless explicitly requested).
- Keep screenshots untracked; attach to PR.
- Cover each changed state (default/loading/success/error/empty/responsive as applicable).
- Prefer JPG/JPEG; use PNG only when required.
- Ensure PR images match latest head commit and are free of unrelated auth/setup errors.
- Use Playwright Firefox in this environment.

### Required UI evidence flow

1. Install tooling: `cd tools/demo && yarn install --frozen-lockfile`
2. Mirror auth/config into `UI_DATA_DIR=/workspace/tmp/agent-hub-ui-evidence`.
3. Start app with mirrored config and real backend.
4. Verify auth endpoint before capture: `curl -fsS http://127.0.0.1:8876/api/settings/auth`
5. Capture screenshots against `http://127.0.0.1:8876`.
6. Upload to public URLs and update PR body image links.
7. Stop evidence processes.

### High-impact UI gotchas

- Docker-in-Docker mounts must be daemon-visible; avoid container-local `/tmp` mount sources.
- Failure signature: `Failed to read config file ... config.toml: Is a directory`.
- Chats UI selectors vary by layout engine; avoid single-structure selectors.
- For startup evidence, validate with `/api/state` + explicit failure-copy checks.

## 6) Git workflow

- Rebase-only workflow; do not merge.
- Branch format: `<username>/<feature-name>`.
- Keep one effective commit per feature branch (amend/squash as needed).
- Rebase onto latest default remote branch before final handoff.
- Force-push updated feature branches when needed.
- Do not commit non-operational artifacts unless explicitly requested.

## 7) Maintenance

- Keep prompt-context docs minimal, current, and operational.
- Record recurring high-cost failures in `docs/agent-gotchas.md`.
- Gotcha format: symptom, root cause, first-try fix, verification, scope.
