export function normalizeCreateProjectConfigMode(value) {
  return String(value || "").trim().toLowerCase() === "manual" ? "manual" : "auto";
}

export function isAutoCreateProjectConfigMode(value) {
  return normalizeCreateProjectConfigMode(value) === "auto";
}

export function shouldShowManualProjectConfigInputs(value) {
  return normalizeCreateProjectConfigMode(value) === "manual";
}
