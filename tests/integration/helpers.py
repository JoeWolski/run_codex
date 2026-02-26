from __future__ import annotations

from typing import Any

import agent_hub.server as hub_server


def insert_ready_project(
    state: hub_server.HubState,
    *,
    project_id: str = "project-1",
    name: str = "project",
    repo_url: str = "https://example.test/repo.git",
    setup_script: str = "",
) -> dict[str, Any]:
    now = hub_server._iso_now()
    project = {
        "id": project_id,
        "name": name,
        "repo_url": repo_url,
        "setup_script": setup_script,
        "base_image_mode": "tag",
        "base_image_value": hub_server.DEFAULT_AGENT_IMAGE,
        "default_ro_mounts": [],
        "default_rw_mounts": [],
        "default_env_vars": [],
        "default_branch": "main",
        "created_at": now,
        "updated_at": now,
        "setup_snapshot_image": "snapshot:test",
        "build_status": "ready",
        "build_error": "",
        "build_started_at": now,
        "build_finished_at": now,
        "repo_head_sha": "abc123",
        "credential_binding": hub_server._normalize_project_credential_binding(None),
    }
    state_data = state.load()
    # Force snapshot tag consistency with project payload so start/launch-profile checks pass.
    project["setup_snapshot_image"] = state._project_setup_snapshot_tag(project)
    state_data["projects"][project_id] = project
    state.save(state_data, reason="test_insert_ready_project")
    return project
