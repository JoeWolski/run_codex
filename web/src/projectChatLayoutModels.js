export function buildProjectChatFlexModels(
  layoutsByProjectId,
  parseModelFromJson,
  onParseError = () => {}
) {
  if (typeof parseModelFromJson !== "function") {
    throw new TypeError("buildProjectChatFlexModels requires a parseModelFromJson function.");
  }
  if (typeof onParseError !== "function") {
    throw new TypeError("buildProjectChatFlexModels onParseError must be a function.");
  }
  const parsedModelsByProjectId = {};
  for (const [projectId, projectLayoutJson] of Object.entries(layoutsByProjectId || {})) {
    if (
      !projectLayoutJson ||
      typeof projectLayoutJson !== "object" ||
      !projectLayoutJson.layout ||
      typeof projectLayoutJson.layout !== "object"
    ) {
      continue;
    }
    try {
      parsedModelsByProjectId[projectId] = parseModelFromJson(projectLayoutJson);
    } catch (err) {
      onParseError(projectId, err);
    }
  }
  return parsedModelsByProjectId;
}
