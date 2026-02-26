# Agent Gotchas

Purpose: record recurring high-cost failures and first-try fixes.

## Entry format

- Symptom
- Root cause
- First-try fix
- Verification
- Scope

## Known gotchas

### PR body update via `gh pr edit` fails

- Symptom: `gh pr edit --body-file ...` fails (GraphQL/Projects deprecation path).
- Root cause: `gh pr edit` can hit deprecated GraphQL fields in this environment.
- First-try fix: `gh api repos/<owner>/<repo>/pulls/<number> -X PATCH --raw-field body="$(cat <body-file>)"`
- Verification: confirm updated `body` in API response or PR page.
- Scope: PR body edits in this repository environment.

### Docker-in-Docker config mount resolves as directory

- Symptom: `Failed to read config file ... config.toml: Is a directory`.
- Root cause: mount source path is not daemon-visible as the expected file.
- First-try fix: move runtime inputs to daemon-visible host paths; avoid container-local `/tmp` mount sources.
- Verification: mounted config path is a regular file from daemon perspective; chat starts cleanly.
- Scope: hub/tests launching runtime containers through host daemon.
