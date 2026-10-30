// Feature flags fixed at build time, read in one place (like lib/env.js). Off unless the build asks.

/**
 * UPI Autopay (M5 §3.7): pilot only. /v1/mandates is not in openapi.json, so with the flag off the
 * Autopay choice and screens are hidden and no mandate request is ever made.
 */
export const AUTOPAY = import.meta.env.VITE_FEATURE_AUTOPAY === "true";

/**
 * Green delivery slots (M3 §3.6, M10 §3.8): a household moves this week's box to another slot on the
 * same date and sees what that saves. The API itself is off by default (GREEN_SLOTS_ENABLED), so with
 * the flag off the entry is hidden and no slot request is ever made.
 */
export const GREEN_SLOTS = import.meta.env.VITE_FEATURE_GREEN_SLOTS === "true";
