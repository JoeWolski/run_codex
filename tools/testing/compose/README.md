# Local Forge Compose Stack

This directory provides a deterministic local forge stack for integration tests that need real
git-over-HTTP provider behavior with minimal external dependencies.

## Services

- `gitea`: local forge API + git host.

## Usage

Start:

```bash
docker compose -f tools/testing/compose/docker-compose.gitea.yml up -d
```

Wait for readiness:

```bash
curl -fsS http://127.0.0.1:3000/api/healthz
```

Stop:

```bash
docker compose -f tools/testing/compose/docker-compose.gitea.yml down -v
```

## Notes

- Compose state is rooted at `/workspace/tmp/agent-hub-gitea-data` so the daemon can resolve mounts
  in Docker-in-Docker setups.
- The current integration suite keeps a lightweight in-process forge for default CI speed. Use this
  compose stack when validating full local provider workflows manually or in dedicated jobs.
