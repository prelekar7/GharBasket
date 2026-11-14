import { toast } from "@gharbasket/ui";
import { openExternal } from "@/shared/native/links.js";
import { shareContent } from "@/shared/native/share.js";

/**
 * Invite neighbours to a society: the phone's share sheet (the Android one in the app, Web Share
 * in a browser), or the link copied with a toast where sharing is not available. Closing the
 * share sheet is not an error.
 * @param {{ societyId: string, slug?: string, text: string, copied: string }} invite `slug` is the society's slug for a prettier link; the id works too
 */
export async function shareInvite({ societyId, slug, text, copied }) {
  const url = inviteLink(slug ?? societyId);
  if ((await shareContent({ title: "GharBasket", text, url })) === "copied") toast(copied);
}

/**
 * The public web address neighbours open. Inside Capacitor `location.origin` is the app's own
 * `https://localhost`, so the link must come from configuration (VITE_PUBLIC_WEB_URL).
 */
const PUBLIC_WEB_URL = (import.meta.env.VITE_PUBLIC_WEB_URL || "https://gharbasket.co.in").replace(
  /\/+$/,
  "",
);

/** The society's landing page that an invite points to (M9 `/s/<society>`, by slug). @param {string} societySlug */
export const inviteLink = (societySlug) => `${PUBLIC_WEB_URL}/s/${encodeURIComponent(societySlug)}`;

/** WhatsApp with the message ready to send (wa.me opens the WhatsApp app, or the web). @param {string} text */
export const whatsappShare = (text) =>
  openExternal(`https://wa.me/?text=${encodeURIComponent(text)}`);

/** A chat with one person on WhatsApp, e.g. the Society Captain. @param {string} phoneE164 */
export const whatsappChat = (phoneE164) =>
  openExternal(`https://wa.me/${phoneE164.replace(/\D/g, "")}`);
