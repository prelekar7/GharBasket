// Where tokens live (Part 3). Web: nowhere in JS, because the API sets httpOnly cookies.
// Native: the access token in memory only, the refresh token in Keystore-backed secure storage.
// The storage plugin is the provisional choice from spike S-2, still to confirm on a device.

const NATIVE = import.meta.env.MODE === "native";
/** @type {string | null} */
let access = null;

/** @type {Promise<void> | null} */
let prefixed = null;

/**
 * Runs `fn` with the secure-storage plugin. Capacitor plugin objects answer any property, `then`
 * included, so one must never be returned from an async function or awaited: it would hang.
 * @template T
 * @param {(storage: typeof import("@aparajita/capacitor-secure-storage").SecureStorage) => Promise<T>} fn
 * @returns {Promise<T>}
 */
async function withSecureStorage(fn) {
  const { SecureStorage } = await import("@aparajita/capacitor-secure-storage");
  prefixed ??= SecureStorage.setKeyPrefix("gb_");
  await prefixed;
  return fn(SecureStorage);
}

export const tokens = {
  /** Current access token (native only; null on web). */
  get access() {
    return access;
  },
  /**
   * Keep the tokens from /v1/auth/exchange or /v1/auth/refresh. Web ignores them.
   * @param {{ access_token?: string, refresh_token?: string }} pair
   */
  async save(pair) {
    if (!NATIVE) return;
    access = pair.access_token ?? null;
    if (pair.refresh_token)
      await withSecureStorage((s) => s.set("refresh_token", pair.refresh_token ?? ""));
  },
  /** @returns {Promise<string | null>} */
  async refreshToken() {
    if (!NATIVE) return null;
    const value = await withSecureStorage((s) => s.get("refresh_token"));
    return typeof value === "string" ? value : null;
  },
  async clear() {
    access = null;
    if (NATIVE) await withSecureStorage((s) => s.remove("refresh_token"));
  },
};
