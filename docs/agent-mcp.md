# Agent MCP

## Artifacts

- Use `submit_artifact` for user-requested deliverables.
- Publish in the same turn.
- Prefer repo-relative paths.
- Do not archive unless explicitly requested.

## Git auth recovery

If clone/pull/push/PR fails for auth:
1. `credentials_list`
2. `credentials_resolve` (`auto` or `single`)
3. `project_attach_credentials`

If no credentials exist, report the missing requirement.

## MCP usage

- Prefer tool-driven deterministic actions.
- Surface exact command errors and evidence.
- Summarize side effects clearly.
