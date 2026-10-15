// Build-time switches, read in one place.

/** Capacitor build (`--mode native`): Bearer tokens and an absolute API URL. */
export const NATIVE = import.meta.env.MODE === "native";

/**
 * The MSW mock API answers only when asked for, and only on the dev server. Vite turns `DEV` into
 * `false` in every build, so a production web or APK build drops the mocks entirely, whatever
 * the env says, and nobody can sign in as a test phone there.
 */
export const MOCK = import.meta.env.DEV && import.meta.env.VITE_API_MOCK === "true";

/** Absolute API origin for native builds. Empty would send requests to https://localhost. */
export const API_BASE = NATIVE ? (import.meta.env.VITE_API_BASE_URL ?? "") : "";
if (NATIVE && !MOCK && !API_BASE)
  throw new Error("VITE_API_BASE_URL is empty: a native build needs the API URL (pnpm env:pull).");
