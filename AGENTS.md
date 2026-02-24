# AGENTS.md

## Core instructions

- In this repository, "core instructions" refers to `SYSTEM_PROMPT.md`.
- Treat `SYSTEM_PROMPT.md` as the authoritative shared system prompt source across all agents.

## Design practices

- Prefer responsive, optimistic UI behavior.
- When users request images of UI/UX elements, capture screenshots from the real running application and real backend flow. Do not provide mockups or mocked-backend renderings unless the user explicitly asks for a mock.
- Any PR that includes UI/UX behavior or layout changes must include image evidence for all changed states against the real backend. Do not consider a UI/UX PR complete until this evidence is attached to the PR.
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
- Time every new individual test in isolation.
- If any new individual test takes longer than 1 second, fix it before merge.
- Long or hanging unit tests are not acceptable.
- When updating, fixing, or adding features for a specific agent, verify the changes do not break or degrade behavior for any other supported agent.

## UI Evidence Workflow

- Never commit screenshot files to the repository. Keep UI evidence files untracked and attach them to the PR.
- For UI/UX PRs, capture every state changed by the PR (for example: default, toggled, loading, success, error, empty, and responsive/mobile variants when relevant).
- Prefer JPG/JPEG for PR UI evidence uploads to reduce file size and improve review loading speed. Use PNG only when PNG is required (for example transparency or lossless detail checks).
- In the development Docker image, use Playwright Firefox for UI evidence capture (`firefox` browser type). This is the browser runtime expected to be available in-container by default.
- Prefer Playwright scripts that explicitly launch Firefox (`const { firefox } = await import("playwright");`) when generating PR screenshots from the real app.
- If browser/runtime availability changes in `docker/development/Dockerfile` (or any image layer it depends on), update this `UI Evidence Workflow` section in the same PR so the documented screenshot browser instructions remain accurate.
- Treat any change that can alter rendered UI output (for example CSS, spacing/alignment, typography, colors, component structure, visibility conditions, state labels/text, or responsive behavior) as requiring refreshed UI evidence.
- After each commit or force-push that includes any UI-rendering change, regenerate and replace PR UI images before handoff, even if the visual change seems small.
- Whenever a PR is updated, explicitly re-check whether existing UI/UX demo images are still accurate for the latest commit. If anything changed visually, regenerate and replace the images in the PR.
- UI/UX images in the PR body must always reflect the most up-to-date UI state for the current PR head commit. Remove or replace stale images immediately.
- Before presenting UI/UX evidence to the user or adding it to the PR body, inspect every image to confirm it shows the intended UI state and not an unrelated error, auth issue, setup failure, or transient warning state.
- Use this exact workflow so image generation is deterministic and does not require rediscovery:
- 1. Install browser tooling: `cd tools/demo && npm ci`
- 2. Mirror auth/config context from the active server before launching the evidence server (so screenshots reflect real connected state and do not show unrelated credential/setup errors):
- `export SOURCE_DATA_DIR=\"${SOURCE_DATA_DIR:-$HOME/.local/share/agent-hub}\"`
- `export UI_DATA_DIR=/tmp/agent-hub-ui-evidence`
- `mkdir -p \"$UI_DATA_DIR\" \"$UI_DATA_DIR/secrets\" \"$HOME/.agent-home/uid-$(id -u)/.codex\"`
- `if [ -d \"$SOURCE_DATA_DIR/secrets\" ]; then rm -rf \"$UI_DATA_DIR/secrets\" && mkdir -p \"$UI_DATA_DIR/secrets\" && cp -a \"$SOURCE_DATA_DIR/secrets/.\" \"$UI_DATA_DIR/secrets/\"; fi`
- `if [ -f \"$HOME/.codex/auth.json\" ]; then cp \"$HOME/.codex/auth.json\" \"$HOME/.agent-home/uid-$(id -u)/.codex/auth.json\"; fi`
- `cp config/agent.config.toml \"$UI_DATA_DIR/agent.config.toml\"`
- `cp SYSTEM_PROMPT.md \"$UI_DATA_DIR/SYSTEM_PROMPT.md\"`
- 3. Start real app server in one terminal with mirrored config context: `UV_PROJECT_ENVIRONMENT=.venv-local uv run agent_hub --host 127.0.0.1 --port 8876 --data-dir \"$UI_DATA_DIR\" --config-file \"$UI_DATA_DIR/agent.config.toml\" --system-prompt-file \"$UI_DATA_DIR/SYSTEM_PROMPT.md\" --frontend-build`
- 4. Sanity-check auth before screenshots (example: `curl -fsS http://127.0.0.1:8876/api/settings/auth`) and do not proceed while unrelated credential/setup errors are visible in UI state targeted for evidence.
- 5. Capture screenshots in another terminal using Playwright against `http://127.0.0.1:8876` (real backend) and save to `.agent-artifacts/` (or `/tmp/agent-hub-ui-evidence/`). Prefer `type: "jpeg"`/`.jpg` outputs unless PNG is explicitly needed.
- 6. For PR body image rendering on `github.com`, use publicly reachable image URLs. Do not use local-only links such as `/api/chats/.../artifacts/...` in PR markdown.
- 7. Programmatic upload path for public URLs (CLI-safe): `curl -fsS -F "file=@<image-path>" https://tmpfiles.org/api/v1/upload` and use the returned URL converted from `https://tmpfiles.org/<id>/<name>` to `https://tmpfiles.org/dl/<id>/<name>`.
- 8. Update the PR body with a `## UI/UX Demo` section containing Markdown image links (`![alt](https://...)`) using `gh api repos/<owner>/<repo>/pulls/<pr-number> -X PATCH --raw-field body=\"$(cat <body-file>)\"`.
- 9. If using GitHub web manually, drag/drop uploads in the PR editor are allowed, but the resulting images still must appear in the PR body.
- 10. Once UI/UX PR evidence is finished and the PR is updated, shut down any programs (such as the app server or Playwright/headless browsers) launched to generate it.
- In the PR Validation section, list the exact commands used for server start, screenshot capture, public URL upload, and PR body update, with pass/fail status.

## Git safety

- This repository uses a rebase-based workflow.
- Keep each branch as a single commit at all times; amend or squash instead of stacking multiple commits.
- If you find a feature branch contains multiple commits, collapse it to a single effective commit and use a sensible commit message that describes the resulting change.
- Never use merges for branch updates or integration; rebase onto the latest default remote branch instead.
- `git push --force` is allowed on updated feature branches.
- Never commit non-operational files (for example: generated media, temporary exports, logs, screenshots, or local debug artifacts). Commit only files required for runtime/build/test behavior (source/config/dependency metadata) unless the user explicitly asks to include non-operational files.
