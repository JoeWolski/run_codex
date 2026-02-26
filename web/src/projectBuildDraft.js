export function selectProjectBuildDraft({ isEditing = false, cachedDraft = null, serverProjectDraft = null }) {
  if (isEditing) {
    return cachedDraft || serverProjectDraft || null;
  }
  return serverProjectDraft || cachedDraft || null;
}
