function cloneJson(value, fallbackValue) {
  const source = value === null || value === undefined ? fallbackValue : value;
  try {
    const cloned = JSON.parse(JSON.stringify(source));
    if (cloned === null || cloned === undefined) {
      return fallbackValue;
    }
    return cloned;
  } catch {
    return fallbackValue;
  }
}

function normalizeLayoutRootNode(node) {
  if (!node || typeof node !== "object" || !Array.isArray(node.children)) {
    return { type: "row", weight: 100, children: [] };
  }
  if (!String(node.type || "").trim()) {
    node.type = "row";
  }
  return node;
}

function isLayoutContainerNode(node) {
  if (!node || typeof node !== "object") {
    return false;
  }
  const type = String(node.type || "");
  return (type === "row" || type === "column") && Array.isArray(node.children);
}

function isLayoutTabsetNode(node) {
  return Boolean(node && typeof node === "object" && String(node.type || "") === "tabset" && Array.isArray(node.children));
}

function isLayoutTabNode(node) {
  return Boolean(node && typeof node === "object" && String(node.type || "") === "tab");
}

function visitLayoutNodes(node, visitor) {
  if (!node || typeof node !== "object") {
    return;
  }
  visitor(node);
  if (!Array.isArray(node.children)) {
    return;
  }
  for (const child of node.children) {
    visitLayoutNodes(child, visitor);
  }
}

function collectTabsetNodes(layoutNode) {
  const tabsets = [];
  visitLayoutNodes(layoutNode, (node) => {
    if (isLayoutTabsetNode(node)) {
      tabsets.push(node);
    }
  });
  return tabsets;
}

function firstTabsetNode(layoutNode) {
  return collectTabsetNodes(layoutNode)[0] || null;
}

function activeTabsetNode(layoutNode) {
  const tabsets = collectTabsetNodes(layoutNode);
  return tabsets.find((tabset) => Boolean(tabset.active)) || null;
}

function normalizeTabsetSelectedIndex(tabset) {
  if (!isLayoutTabsetNode(tabset)) {
    return;
  }
  const childCount = tabset.children.length;
  if (childCount <= 0) {
    tabset.selected = 0;
    return;
  }
  const selected = Number(tabset.selected);
  if (Number.isInteger(selected) && selected >= 0 && selected < childCount) {
    return;
  }
  tabset.selected = 0;
}

function outerProjectTabId(projectId) {
  return `project-${String(projectId || "").trim()}`;
}

function chatPaneTabId(chatId) {
  return `chat-${String(chatId || "").trim()}`;
}

function projectIdFromOuterTab(tabNodeJson) {
  const configuredProjectId = String(tabNodeJson?.config?.project_id || "").trim();
  if (configuredProjectId) {
    return configuredProjectId;
  }
  const id = String(tabNodeJson?.id || "").trim();
  if (id.startsWith("project-")) {
    return id.slice("project-".length);
  }
  return "";
}

function chatIdFromProjectPaneTab(tabNodeJson) {
  const configuredChatId = String(tabNodeJson?.config?.chat_id || "").trim();
  if (configuredChatId) {
    return configuredChatId;
  }
  const id = String(tabNodeJson?.id || "").trim();
  if (id.startsWith("chat-")) {
    return id.slice("chat-".length);
  }
  return "";
}

function buildOuterProjectTabNode(project) {
  return {
    type: "tab",
    id: outerProjectTabId(project.id),
    name: String(project.name || "Project"),
    component: "project-chat-group",
    config: { project_id: project.id }
  };
}

function buildOuterOrphanTabNode() {
  return {
    type: "tab",
    id: "orphan-chats",
    name: "Unknown project",
    component: "orphan-chat-group",
    config: {}
  };
}

function buildProjectChatPaneTabNode(chat) {
  return {
    type: "tab",
    id: chatPaneTabId(chat.id),
    name: String(chat.display_name || chat.name || "Chat"),
    component: "project-chat-pane",
    config: { chat_id: chat.id }
  };
}

function normalizeLayoutGlobalSettings(existingGlobal) {
  return {
    ...(existingGlobal || {}),
    tabEnableClose: false,
    tabSetEnableDeleteWhenEmpty: true,
    tabSetEnableMaximize: false
  };
}

function buildDefaultOuterFlexLayoutJson(projects, includeOrphanChats = false) {
  const children = projects.map((project) => buildOuterProjectTabNode(project));
  if (includeOrphanChats) {
    children.push(buildOuterOrphanTabNode());
  }
  return {
    global: normalizeLayoutGlobalSettings(null),
    borders: [],
    layout: {
      type: "row",
      weight: 100,
      children: [
        {
          type: "tabset",
          id: "chat-layout-main-tabset",
          weight: 100,
          selected: 0,
          active: true,
          children
        }
      ]
    }
  };
}

function buildDefaultProjectChatsFlexLayoutJson(chats, projectId) {
  return {
    global: normalizeLayoutGlobalSettings(null),
    borders: [],
    layout: {
      type: "row",
      weight: 100,
      children: [
        {
          type: "tabset",
          id: `project-${projectId}-chat-tabset-main`,
          weight: 100,
          selected: 0,
          active: true,
          children: chats.map((chat) => buildProjectChatPaneTabNode(chat))
        }
      ]
    }
  };
}

function normalizeSingleActiveTabset(tabsets, preferredTabset = null) {
  let active = preferredTabset && tabsets.includes(preferredTabset) ? preferredTabset : null;
  if (!active) {
    active = tabsets.find((tabset) => Boolean(tabset.active)) || tabsets[0] || null;
  }
  for (const tabset of tabsets) {
    if (active && tabset === active) {
      tabset.active = true;
      continue;
    }
    delete tabset.active;
  }
  return active;
}

export function pruneEmptyLayoutNode(node) {
  if (!node || typeof node !== "object") {
    return null;
  }
  if (isLayoutTabsetNode(node)) {
    node.children = (node.children || []).filter((child) => isLayoutTabNode(child));
    normalizeTabsetSelectedIndex(node);
    if (node.children.length === 0) {
      return null;
    }
    return node;
  }
  if (!isLayoutContainerNode(node)) {
    return node;
  }
  const nextChildren = [];
  for (const child of node.children) {
    const normalizedChild = pruneEmptyLayoutNode(child);
    if (normalizedChild) {
      nextChildren.push(normalizedChild);
    }
  }
  node.children = nextChildren;
  if (node.children.length === 0) {
    return null;
  }
  if (node.children.length === 1) {
    const onlyChild = node.children[0];
    if (onlyChild && typeof onlyChild === "object") {
      if (onlyChild.weight === undefined && node.weight !== undefined) {
        onlyChild.weight = node.weight;
      }
      return onlyChild;
    }
  }
  return node;
}

function normalizeLayoutTreeRoot(layoutNode) {
  const normalized = normalizeLayoutRootNode(pruneEmptyLayoutNode(layoutNode));
  if (String(normalized.type || "") === "tabset") {
    return {
      type: "row",
      weight: 100,
      children: [normalized]
    };
  }
  return normalized;
}

function ensureTabsetInRoot(layoutJson, tabsetId) {
  let tabsets = collectTabsetNodes(layoutJson.layout);
  if (tabsets.length > 0) {
    return tabsets;
  }
  layoutJson.layout.children = [
    {
      type: "tabset",
      id: tabsetId,
      weight: 100,
      selected: 0,
      active: true,
      children: []
    }
  ];
  tabsets = collectTabsetNodes(layoutJson.layout);
  return tabsets;
}

export function layoutJsonEquals(left, right) {
  try {
    return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
  } catch {
    return false;
  }
}

export function reconcileOuterFlexLayoutJson(existingLayoutJson, projects, includeOrphanChats = false) {
  if (projects.length === 0 && !includeOrphanChats) {
    return null;
  }
  const layoutJson = cloneJson(
    existingLayoutJson,
    buildDefaultOuterFlexLayoutJson(projects, includeOrphanChats)
  );
  layoutJson.global = normalizeLayoutGlobalSettings(layoutJson.global);
  layoutJson.borders = [];
  layoutJson.layout = normalizeLayoutTreeRoot(layoutJson.layout);

  let tabsets = ensureTabsetInRoot(layoutJson, "chat-layout-main-tabset");
  const projectsById = new Map(projects.map((project) => [String(project.id || ""), project]));
  const seenProjectIds = new Set();
  let orphanTabSeen = false;

  for (const tabset of tabsets) {
    const nextChildren = [];
    for (const child of tabset.children || []) {
      if (!isLayoutTabNode(child)) {
        continue;
      }
      const component = String(child.component || "");
      if (component === "project-chat-group") {
        const projectId = projectIdFromOuterTab(child);
        if (!projectId || !projectsById.has(projectId) || seenProjectIds.has(projectId)) {
          continue;
        }
        seenProjectIds.add(projectId);
        const project = projectsById.get(projectId);
        nextChildren.push({
          ...child,
          id: outerProjectTabId(projectId),
          name: String(project.name || child.name || "Project"),
          component: "project-chat-group",
          config: { ...(child.config || {}), project_id: projectId }
        });
        continue;
      }
      if (component === "orphan-chat-group" || String(child.id || "") === "orphan-chats") {
        if (!includeOrphanChats || orphanTabSeen) {
          continue;
        }
        orphanTabSeen = true;
        nextChildren.push({
          ...child,
          id: "orphan-chats",
          name: "Unknown project",
          component: "orphan-chat-group",
          config: {}
        });
      }
    }
    tabset.children = nextChildren;
    normalizeTabsetSelectedIndex(tabset);
  }

  tabsets = collectTabsetNodes(layoutJson.layout);
  const targetTabset = activeTabsetNode(layoutJson.layout) || firstTabsetNode(layoutJson.layout) || tabsets[0];
  if (!targetTabset) {
    return buildDefaultOuterFlexLayoutJson(projects, includeOrphanChats);
  }

  for (const project of projects) {
    const projectId = String(project.id || "");
    if (!projectId || seenProjectIds.has(projectId)) {
      continue;
    }
    targetTabset.children.push(buildOuterProjectTabNode(project));
    seenProjectIds.add(projectId);
  }
  if (includeOrphanChats && !orphanTabSeen) {
    targetTabset.children.push(buildOuterOrphanTabNode());
  }

  layoutJson.layout = normalizeLayoutTreeRoot(layoutJson.layout);
  tabsets = ensureTabsetInRoot(layoutJson, "chat-layout-main-tabset");
  const active = normalizeSingleActiveTabset(tabsets, targetTabset);
  for (const tabset of tabsets) {
    normalizeTabsetSelectedIndex(tabset);
  }
  if (!active) {
    return buildDefaultOuterFlexLayoutJson(projects, includeOrphanChats);
  }
  return layoutJson;
}

export function reconcileProjectChatsFlexLayoutJson(existingLayoutJson, chats, projectId) {
  if (chats.length === 0) {
    return null;
  }
  const layoutJson = cloneJson(
    existingLayoutJson,
    buildDefaultProjectChatsFlexLayoutJson(chats, projectId)
  );
  layoutJson.global = normalizeLayoutGlobalSettings(layoutJson.global);
  layoutJson.borders = [];
  layoutJson.layout = normalizeLayoutTreeRoot(layoutJson.layout);

  let tabsets = ensureTabsetInRoot(layoutJson, `project-${projectId}-chat-tabset-main`);
  const chatsById = new Map(chats.map((chat) => [String(chat.id || ""), chat]));
  const seenChatIds = new Set();

  for (const tabset of tabsets) {
    const nextChildren = [];
    for (const child of tabset.children || []) {
      if (!isLayoutTabNode(child)) {
        continue;
      }
      const component = String(child.component || "");
      if (component !== "project-chat-pane") {
        continue;
      }
      const chatId = chatIdFromProjectPaneTab(child);
      if (!chatId || !chatsById.has(chatId) || seenChatIds.has(chatId)) {
        continue;
      }
      const chat = chatsById.get(chatId);
      seenChatIds.add(chatId);
      nextChildren.push({
        ...child,
        id: chatPaneTabId(chatId),
        name: String(chat.display_name || chat.name || child.name || "Chat"),
        component: "project-chat-pane",
        config: { ...(child.config || {}), chat_id: chatId }
      });
    }
    tabset.children = nextChildren;
    normalizeTabsetSelectedIndex(tabset);
  }

  tabsets = collectTabsetNodes(layoutJson.layout);
  const targetTabset = activeTabsetNode(layoutJson.layout) || firstTabsetNode(layoutJson.layout) || tabsets[0];
  if (!targetTabset) {
    return buildDefaultProjectChatsFlexLayoutJson(chats, projectId);
  }

  for (const chat of chats) {
    const chatId = String(chat.id || "");
    if (!chatId || seenChatIds.has(chatId)) {
      continue;
    }
    targetTabset.children.push(buildProjectChatPaneTabNode(chat));
    seenChatIds.add(chatId);
  }

  layoutJson.layout = normalizeLayoutTreeRoot(layoutJson.layout);
  tabsets = ensureTabsetInRoot(layoutJson, `project-${projectId}-chat-tabset-main`);
  normalizeSingleActiveTabset(tabsets, targetTabset);
  for (const tabset of tabsets) {
    normalizeTabsetSelectedIndex(tabset);
  }
  return layoutJson;
}
