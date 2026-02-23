export const PENDING_SESSION_STALE_MS = 30_000;
export const PENDING_CHAT_START_STALE_MS = 30_000;

function safeTimestampMs(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 0;
  }
  return parsed;
}

function pendingStartTimestampMs(value, fallbackNowMs = 0) {
  const directTimestamp = safeTimestampMs(value);
  if (directTimestamp > 0) {
    return directTimestamp;
  }
  if (value && typeof value === "object") {
    const fromObject = safeTimestampMs(value.started_at_ms);
    if (fromObject > 0) {
      return fromObject;
    }
  }
  if (value === true) {
    return safeTimestampMs(fallbackNowMs);
  }
  return 0;
}

export function isChatStarting(status, isRunning, isPendingStart) {
  if (Boolean(isRunning)) {
    return false;
  }
  const normalizedStatus = String(status || "").toLowerCase();
  if (normalizedStatus === "starting") {
    return true;
  }
  return Boolean(isPendingStart);
}

export function reconcilePendingSessions(previousSessions, serverChatsById, nowMs = Date.now()) {
  const sessions = Array.isArray(previousSessions) ? previousSessions : [];
  const serverMap = serverChatsById instanceof Map ? serverChatsById : new Map();
  const currentTimeMs = safeTimestampMs(nowMs) || Date.now();

  const next = [];
  for (const session of sessions) {
    if (!session || typeof session !== "object") {
      continue;
    }
    const serverChatId = String(session.server_chat_id || "");
    if (!serverChatId) {
      next.push(session);
      continue;
    }

    const onServer = serverMap.has(serverChatId);
    const seenOnServer = Boolean(session.seen_on_server || onServer);
    if (seenOnServer && !onServer) {
      continue;
    }

    const serverChatIdSetAtMs = safeTimestampMs(session.server_chat_id_set_at_ms);
    const createdAtMs = safeTimestampMs(session.created_at_ms);
    const staleSinceMs = serverChatIdSetAtMs || createdAtMs;
    if (!onServer && !seenOnServer && staleSinceMs > 0 && currentTimeMs - staleSinceMs >= PENDING_SESSION_STALE_MS) {
      continue;
    }

    if (seenOnServer !== Boolean(session.seen_on_server)) {
      next.push({ ...session, seen_on_server: seenOnServer });
      continue;
    }

    next.push(session);
  }
  return next;
}

export function reconcilePendingChatStarts(previousPendingChatStarts, serverChatsById, nowMs = Date.now()) {
  const pending = previousPendingChatStarts && typeof previousPendingChatStarts === "object"
    ? previousPendingChatStarts
    : {};
  const serverMap = serverChatsById instanceof Map ? serverChatsById : new Map();
  const currentTimeMs = safeTimestampMs(nowMs) || Date.now();
  const next = {};
  for (const [chatId, pendingValue] of Object.entries(pending)) {
    const startTimestampMs = pendingStartTimestampMs(pendingValue, currentTimeMs);
    if (startTimestampMs <= 0) {
      continue;
    }

    const isStale = currentTimeMs - startTimestampMs >= PENDING_CHAT_START_STALE_MS;
    if (isStale) {
      continue;
    }

    const serverChat = serverMap.get(chatId);
    if (!serverChat) {
      next[chatId] = startTimestampMs;
      continue;
    }

    const normalizedStatus = String(serverChat.status || "").toLowerCase();
    if (normalizedStatus === "failed") {
      continue;
    }

    const isRunning = Boolean(serverChat.is_running);
    if (!isRunning) {
      next[chatId] = startTimestampMs;
    }
  }
  return next;
}
