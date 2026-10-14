// Safari must ask from a tap. Share in-flight requests and remember grants; a failed pre-tap attempt can retry.
const pending = new Map(), granted = new Set();
export function motionPermission(eventType) {
  if (!eventType || globalThis.isSecureContext === false) return Promise.resolve(false);
  if (granted.has(eventType)) return Promise.resolve(true);
  if (pending.has(eventType)) return pending.get(eventType);
  if (typeof eventType.requestPermission !== "function") return Promise.resolve(true);
  if (globalThis.navigator?.userActivation?.isActive === false) return Promise.resolve(false);
  let request;
  try { request = eventType.requestPermission(); } catch { return Promise.resolve(false); }
  const result = Promise.resolve(request).then(answer => {
    if (answer === "granted") { granted.add(eventType); return true; }
    return false;
  }, () => false).finally(() => pending.delete(eventType));
  pending.set(eventType, result);
  return result;
}
