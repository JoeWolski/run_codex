function ensureTrailingNewline(text) {
  if (text.endsWith("\n")) {
    return text;
  }
  return `${text}\n`;
}

export function projectRowFromPendingAutoConfig(project) {
  return {
    id: project.id,
    stable_order_key: String(project.stable_order_key || project.id || ""),
    name: project.name,
    repo_url: project.repo_url,
    default_branch: project.default_branch,
    build_status: project.auto_config_status === "failed" ? "failed" : "building",
    build_error: project.auto_config_error || "",
    auto_config_log: project.auto_config_log || "",
    is_auto_config_pending: true
  };
}

export function markPendingAutoConfigProjectFailed(projects, requestId, message) {
  const normalizedRequestId = String(requestId || "");
  const normalizedMessage = String(message || "");
  if (!normalizedRequestId) {
    return Array.isArray(projects) ? [...projects] : [];
  }
  return (projects || []).map((project) =>
    String(project.id || "") === normalizedRequestId
      ? {
        ...project,
        auto_config_status: "failed",
        auto_config_error: normalizedMessage,
        auto_config_log: `${project.auto_config_log || ""}${ensureTrailingNewline(normalizedMessage)}`
      }
      : project
  );
}

export function removePendingAutoConfigProject(projects, requestId) {
  const normalizedRequestId = String(requestId || "");
  return (projects || []).filter((project) => String(project.id || "") !== normalizedRequestId);
}
