# Repo Map

## Core paths

- `src/agent_cli/`: runtime launcher and shared prompt-context assembly.
- `src/agent_hub/`: API/backend for projects, chats, auth, artifacts, and terminal streaming.
- `web/`: frontend UI.

## Prompt-context inputs

- `SYSTEM_PROMPT.md`: shared cross-project core instructions.
- `AGENTS.md`: repo-specific instructions.
- `config/agent.config.toml`: runtime defaults and auto-loaded project-doc list.
- `docs/agent-setup.md`, `docs/agent-mcp.md`, `docs/agent-gotchas.md`: compact operational references.

## Validation path

- `tests/test_hub_and_cli.py`: primary orchestration and prompt-context tests.

## Container path

- `docker/agent_cli/`: chat runtime image.
- `docker/agent_hub/`: production hub image.
- `docker/development/`: development image.
