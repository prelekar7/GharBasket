/**
 * Where the Ghar Saathi conversation is kept for the tab; sign-out removes it (lib/localData.js).
 * The suffix is the shape of what is stored: v2 holds API proposals (summary and payload), so a chat
 * saved by the older build, whose cards had a different shape, is left behind rather than read.
 */
export const ASSISTANT_STORAGE_KEY = "gb-assistant-v2";
