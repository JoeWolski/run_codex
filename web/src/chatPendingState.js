export const PENDING_SESSION_STALE_MS = 30_000;

function safeTimestampMs(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 0;
  }
  return parsed;
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

export function reconcilePendingChatStarts(previousPendingChatStarts, serverChatsById) {
  const pending = previousPendingChatStarts && typeof previousPendingChatStarts === "object"
    ? previousPendingChatStarts
    : {};
  const serverMap = serverChatsById instanceof Map ? serverChatsById : new Map();
  const next = {};
  for (const [chatId, isPending] of Object.entries(pending)) {
    if (!isPending) {
      continue;
    }
    const serverChat = serverMap.get(chatId);
    if (!serverChat) {
      continue;
    }
    const isRunning = Boolean(serverChat.is_running);
    const status = String(serverChat.status || "").toLowerCase();
    if (!isRunning && status === "starting") {
      next[chatId] = true;
    }
  }
  return next;
}
