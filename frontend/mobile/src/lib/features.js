// Feature flags fixed at build time, read in one place (like lib/env.js). Off unless the build asks.

/**
 * UPI Autopay (M5 §3.7): pilot only. /v1/mandates is not in openapi.json, so with the flag off the
 * Autopay choice and screens are hidden and no mandate request is ever made.
 */
export const AUTOPAY = import.meta.env.VITE_FEATURE_AUTOPAY === "true";
