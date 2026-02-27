from __future__ import annotations

import glob
import logging
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SUBMIT_ARTIFACT_MAX_ATTEMPTS_ENV = "AGENT_TOOLS_SUBMIT_ARTIFACT_MAX_ATTEMPTS"
SUBMIT_ARTIFACT_HARD_MAX_ATTEMPTS = 3
SUBMIT_ARTIFACT_RETRY_DELAY_BASE_SEC_ENV = "AGENT_TOOLS_SUBMIT_ARTIFACT_RETRY_DELAY_BASE_SEC"
SUBMIT_ARTIFACT_RETRY_DELAY_MAX_SEC_ENV = "AGENT_TOOLS_SUBMIT_ARTIFACT_RETRY_DELAY_MAX_SEC"

LOGGER = logging.getLogger("agent_tools_mcp")

TOOL_LIST = [
    {
        "name": "credentials_list",
        "description": (
            "List credential options available for the active repository context, "
            "including current project credential binding and effective selection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "credentials_resolve",
        "description": (
            "Resolve credentials for the active repository context. "
            "Supported modes: auto, all, set, single."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "credential_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "project_attach_credentials",
        "description": (
            "Attach a credential binding to the backing project so future chats auto-select "
            "the same credential set without manual token ordering."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "credential_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_artifact",
        "description": (
            "Submit one or more artifact files to Agent Hub for durable download links. "
            "Accepts individual files, glob patterns, or flat directories."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "name": {"type": "string"},
                "max_attempts": {"type": "integer"},
                "retry_delay_base_sec": {"type": "integer"},
                "retry_delay_max_sec": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ack",
        "description": (
            "Acknowledge runtime readiness to Agent Hub for deterministic launch synchronization. "
            "The guid must match the runtime readiness token set by the launcher."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "guid": {"type": "string"},
                "stage": {"type": "string"},
                "meta": {"type": "object"},
            },
            "required": ["guid"],
            "additionalProperties": False,
        },
    },
]


def _env_required(key: str) -> str:
    value = str(os.environ.get(key) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def _agent_tools_base_url() -> str:
    return _env_required("AGENT_HUB_AGENT_TOOLS_URL").rstrip("/")


def _agent_tools_token() -> str:
    return _env_required("AGENT_HUB_AGENT_TOOLS_TOKEN")


def _api_request(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = _agent_tools_base_url()
    url = f"{base_url}{path}"
    token = _agent_tools_token()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-agent-hub-agent-tools-token": token,
        "Authorization": f"Bearer {token}",
        "User-Agent": "agent-tools-mcp/1.0",
    }
    if payload is not None:
        payload_preview = str(payload)
        if len(payload_preview) > 400:
            payload_preview = f"{payload_preview[:400]}..."
    else:
        payload_preview = "None"
    LOGGER.debug(
        "agent_tools API request: method=%s url=%s payload=%s",
        method,
        url,
        payload_preview,
    )
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            body = response.read().decode("utf-8", errors="replace")
            if not body.strip():
                LOGGER.debug("agent_tools API response for %s %s was empty", method, url)
                return {}
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as exc:
                response_preview = body[:400]
                if len(body) > 400:
                    response_preview = f"{response_preview}..."
                LOGGER.error(
                    "agent_tools API response for %s %s was non-JSON: %s body=%s",
                    method,
                    url,
                    exc,
                    response_preview,
                )
                raise RuntimeError(f"agent_tools API request returned non-JSON response: {response_preview}") from exc
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        detail = body.strip() or f"HTTP {exc.code}"
        LOGGER.warning(
            "agent_tools API request failed for %s %s: HTTP %s body=%s",
            method,
            url,
            exc.code,
            body.strip()[:400] if body else "None",
        )
        raise RuntimeError(f"agent_tools API request failed: {method} {url} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        LOGGER.error("agent_tools API request failed for %s %s: %s", method, url, exc)
        raise RuntimeError(f"agent_tools API request failed: {method} {url}: {exc}") from exc


def _tool_response(result: Any) -> dict[str, Any]:
    text = json.dumps(result, indent=2, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
        "isError": False,
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _positive_int(value: Any, *, field_name: str, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field_name} must be an integer >= {minimum}.") from exc
    if parsed < minimum:
        raise RuntimeError(f"{field_name} must be an integer >= {minimum}.")
    return parsed


def _non_negative_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field_name} must be an integer >= 0.") from exc
    if parsed < 0:
        raise RuntimeError(f"{field_name} must be an integer >= 0.")
    return parsed


def _submitted_artifact_paths(arguments: dict[str, Any]) -> list[str]:
    submitted: list[str] = []
    raw_path = arguments.get("path")
    if raw_path is not None:
        normalized = str(raw_path).strip()
        if normalized:
            submitted.append(normalized)

    raw_paths = arguments.get("paths")
    if raw_paths is not None and not isinstance(raw_paths, list):
        raise RuntimeError("paths must be an array of strings.")
    if isinstance(raw_paths, list):
        for index, value in enumerate(raw_paths):
            if not isinstance(value, str):
                raise RuntimeError(f"paths[{index}] must be a string.")
            normalized = value.strip()
            if normalized:
                submitted.append(normalized)

    if not submitted:
        raise RuntimeError("submit_artifact requires at least one path via path or paths.")
    return submitted


def _expand_artifact_file_paths(submitted_paths: list[str]) -> list[Path]:
    upload_paths: list[Path] = []
    for submitted in submitted_paths:
        candidates = [submitted]
        if any(char in submitted for char in "*?["):
            matches = sorted(glob.glob(submitted))
            if not matches:
                raise RuntimeError(f"Artifact file not found: {submitted}")
            candidates = matches

        for raw_candidate in candidates:
            candidate = Path(raw_candidate).expanduser()
            if candidate.is_dir():
                entries = sorted(candidate.iterdir(), key=lambda item: item.name)
                for entry in entries:
                    if entry.is_dir():
                        raise RuntimeError(f"Subdirectories are not supported for artifact submit: {entry}")
                    if not entry.is_file():
                        raise RuntimeError(f"Artifact path is not a regular file: {entry}")
                    upload_paths.append(entry.resolve())
                continue

            if not candidate.exists():
                raise RuntimeError(f"Artifact file not found: {raw_candidate}")
            if not candidate.is_file():
                raise RuntimeError(f"Artifact path is not a regular file: {raw_candidate}")
            upload_paths.append(candidate.resolve())

    if not upload_paths:
        raise RuntimeError("No files found to submit.")
    return upload_paths


def _submit_artifact_path(path: Path, *, name: str = "") -> dict[str, Any]:
    base_url = _agent_tools_base_url()
    url = f"{base_url}/artifacts/submit"
    token = _agent_tools_token()
    file_name = str(name).strip() or path.name or "artifact"
    body = path.read_bytes()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/octet-stream",
        "x-agent-hub-artifact-name": file_name,
        "x-agent-hub-agent-tools-token": token,
        "Authorization": f"Bearer {token}",
        "User-Agent": "agent-tools-mcp/1.0",
    }
    request = urllib.request.Request(url, headers=headers, method="POST", data=body)
    try:
        with urllib.request.urlopen(request, timeout=60.0) as response:
            payload_text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="ignore")
        detail = body_text.strip() or f"HTTP {exc.code}"
        LOGGER.warning(
            "agent_tools multipart artifact submit failed for %s: HTTP %s body=%s",
            path,
            exc.code,
            body_text.strip()[:400] if body_text else "None",
        )
        raise RuntimeError(f"agent_tools API request failed: POST {url} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        LOGGER.error("agent_tools multipart artifact submit failed for %s: %s", path, exc)
        raise RuntimeError(f"agent_tools API request failed: POST {url}: {exc}") from exc

    if not payload_text.strip():
        raise RuntimeError(f"agent_tools artifact submit response missing artifact payload for {path}")
    try:
        response = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        response_preview = payload_text[:400]
        if len(payload_text) > 400:
            response_preview = f"{response_preview}..."
        raise RuntimeError(f"agent_tools API request returned non-JSON response: {response_preview}") from exc
    artifact_payload = response.get("artifact") if isinstance(response, dict) else None
    if not isinstance(artifact_payload, dict):
        raise RuntimeError(f"agent_tools artifact submit response missing artifact payload for {path}")
    return artifact_payload


def _retry_delay_seconds(attempt: int, base_delay_seconds: int, max_delay_seconds: int) -> int:
    if base_delay_seconds <= 0:
        return 0
    scaled = base_delay_seconds * (2 ** max(0, attempt - 1))
    return min(max_delay_seconds, scaled)


def _submit_artifacts(arguments: dict[str, Any]) -> dict[str, Any]:
    submitted_paths = _submitted_artifact_paths(arguments)
    upload_paths = _expand_artifact_file_paths(submitted_paths)
    submitted_name = str(arguments.get("name") or "").strip()
    if submitted_name and len(upload_paths) != 1:
        raise RuntimeError("--name can only be used when submitting exactly one file.")

    max_attempts = _positive_int(
        arguments.get("max_attempts", os.environ.get(SUBMIT_ARTIFACT_MAX_ATTEMPTS_ENV, "3")),
        field_name="max_attempts",
        minimum=1,
    )
    if max_attempts > SUBMIT_ARTIFACT_HARD_MAX_ATTEMPTS:
        LOGGER.warning(
            "submit_artifact max_attempts=%s exceeds hard cap; clamping to %s",
            max_attempts,
            SUBMIT_ARTIFACT_HARD_MAX_ATTEMPTS,
        )
        max_attempts = SUBMIT_ARTIFACT_HARD_MAX_ATTEMPTS
    retry_delay_base_sec = _non_negative_int(
        arguments.get("retry_delay_base_sec", os.environ.get(SUBMIT_ARTIFACT_RETRY_DELAY_BASE_SEC_ENV, "1")),
        field_name="retry_delay_base_sec",
    )
    retry_delay_max_sec = _non_negative_int(
        arguments.get("retry_delay_max_sec", os.environ.get(SUBMIT_ARTIFACT_RETRY_DELAY_MAX_SEC_ENV, "30")),
        field_name="retry_delay_max_sec",
    )

    submitted_artifacts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    LOGGER.debug(
        "submit_artifact queued=%d paths; max_attempts=%s retry_base_sec=%s retry_max_sec=%s",
        len(upload_paths),
        max_attempts,
        retry_delay_base_sec,
        retry_delay_max_sec,
    )

    for path in upload_paths:
        per_file_name = submitted_name if len(upload_paths) == 1 else ""
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                LOGGER.debug("submit_artifact attempt=%s/%s path=%s", attempt, max_attempts, path)
                artifact_payload = _submit_artifact_path(path, name=per_file_name)
                LOGGER.info("submit_artifact succeeded path=%s attempt=%s/%s", path, attempt, max_attempts)
                submitted_artifacts.append(artifact_payload)
                last_error = ""
                break
            except Exception as exc:  # pragma: no cover - error branches are tested via higher-level outcomes.
                last_error = str(exc)
                LOGGER.warning(
                    "submit_artifact attempt=%s/%s failed for path=%s: %s",
                    attempt,
                    max_attempts,
                    path,
                    last_error,
                )
                if attempt >= max_attempts:
                    break
                delay_seconds = _retry_delay_seconds(attempt, retry_delay_base_sec, retry_delay_max_sec)
                if delay_seconds > 0:
                    LOGGER.debug(
                        "submit_artifact retry scheduled path=%s delay_seconds=%s",
                        path,
                        delay_seconds,
                    )
                    time.sleep(delay_seconds)

        if last_error:
            failures.append(
                {
                    "path": str(path),
                    "error": last_error,
                }
            )

    result = {
        "artifacts": submitted_artifacts,
        "processed_count": len(upload_paths),
        "succeeded_count": len(submitted_artifacts),
        "failed_count": len(failures),
        "failed_paths": [entry["path"] for entry in failures],
    }
    if failures:
        failed_paths = [entry["path"] for entry in failures]
        LOGGER.error("submit_artifact failed for %d/%d paths: %s", len(failures), len(upload_paths), failed_paths)
        failure_payload = json.dumps(
            {
                "failed_count": len(failures),
                "failures": failures,
            },
            sort_keys=True,
        )
        raise RuntimeError(f"submit_artifact failed: {failure_payload}")
    return result


def _handle_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "credentials_list":
        payload = _api_request("/credentials", method="GET")
        return _tool_response(payload)
    if name == "credentials_resolve":
        body = {
            "mode": arguments.get("mode", "auto"),
            "credential_ids": arguments.get("credential_ids") or [],
        }
        payload = _api_request("/credentials/resolve", method="POST", payload=body)
        return _tool_response(payload)
    if name == "project_attach_credentials":
        body = {
            "mode": arguments.get("mode", "auto"),
            "credential_ids": arguments.get("credential_ids") or [],
        }
        payload = _api_request("/project-binding", method="POST", payload=body)
        return _tool_response(payload)
    if name == "submit_artifact":
        payload = _submit_artifacts(arguments)
        return _tool_response(payload)
    if name == "ack":
        guid = str(arguments.get("guid") or "").strip()
        if not guid:
            raise RuntimeError("ack requires a non-empty guid.")
        body: dict[str, Any] = {"guid": guid}
        stage = str(arguments.get("stage") or "").strip()
        if stage:
            body["stage"] = stage
        meta = arguments.get("meta")
        if meta is not None:
            if not isinstance(meta, dict):
                raise RuntimeError("meta must be an object.")
            body["meta"] = meta
        payload = _api_request("/ack", method="POST", payload=body)
        return _tool_response(payload)
    return _tool_error(f"Unsupported tool: {name}")


def _write_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _handle_request(request: dict[str, Any]) -> None:
    method = str(request.get("method") or "")
    request_id = request.get("id")
    params = request.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        _write_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agent_tools", "version": "1.0.0"},
                },
            }
        )
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        _write_json({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOL_LIST}})
        return

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = _handle_tool_call(name, arguments)
            _write_json({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            _write_json({"jsonrpc": "2.0", "id": request_id, "result": _tool_error(str(exc))})
        return

    if request_id is not None:
        _write_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        )


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        try:
            _handle_request(parsed)
        except Exception as exc:
            request_id = parsed.get("id")
            if request_id is not None:
                _write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32000,
                            "message": str(exc),
                            "data": traceback.format_exc(limit=2),
                        },
                    }
                )


if __name__ == "__main__":
    main()
