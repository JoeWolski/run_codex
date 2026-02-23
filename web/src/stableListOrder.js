function normalizeOrderKey(value) {
  return String(value ?? "").trim();
}

export function createFirstSeenOrderState() {
  return {
    nextOrder: 0,
    orderByKey: new Map()
  };
}

export function resolveFirstSeenOrderKey(rawKey, aliasMap) {
  let key = normalizeOrderKey(rawKey);
  if (!key || !(aliasMap instanceof Map)) {
    return key;
  }
  const visited = new Set();
  while (key && aliasMap.has(key) && !visited.has(key)) {
    visited.add(key);
    key = normalizeOrderKey(aliasMap.get(key));
  }
  return key;
}

export function stableOrderItemsByFirstSeen(items, keyResolver, state, aliasMap = null) {
  const source = Array.isArray(items) ? items : [];
  if (!state || !(state.orderByKey instanceof Map)) {
    throw new Error("stableOrderItemsByFirstSeen requires a mutable order state from createFirstSeenOrderState().");
  }
  if (typeof keyResolver !== "function") {
    throw new Error("stableOrderItemsByFirstSeen requires a key resolver function.");
  }

  const sortable = [];
  for (let index = 0; index < source.length; index += 1) {
    const item = source[index];
    const rawKey = keyResolver(item, index);
    const resolvedKey = resolveFirstSeenOrderKey(rawKey, aliasMap);
    const orderKey = resolvedKey || `__index__${index}`;
    let firstSeenOrder = state.orderByKey.get(orderKey);
    if (typeof firstSeenOrder !== "number") {
      firstSeenOrder = state.nextOrder;
      state.nextOrder += 1;
      state.orderByKey.set(orderKey, firstSeenOrder);
    }
    sortable.push({ item, firstSeenOrder, sourceIndex: index });
  }

  sortable.sort((left, right) => left.firstSeenOrder - right.firstSeenOrder || left.sourceIndex - right.sourceIndex);
  return sortable.map((entry) => entry.item);
}
