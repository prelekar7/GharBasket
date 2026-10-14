import { toast } from "@gharbasket/ui";
import { useSession } from "@/lib/session.js";

/**
 * The public web address neighbours open. Inside Capacitor `location.origin` is the app's own
 * `https://localhost`, so the link must come from configuration (VITE_PUBLIC_WEB_URL).
 */
const PUBLIC_WEB_URL = (import.meta.env.VITE_PUBLIC_WEB_URL || "https://gharbasket.in").replace(
  /\/+$/,
  "",
);

/**
 * The referral landing link for a society (M9 US-3): `<public url>/s/<society>?ref=<referrer id>`.
 * `ref` is the referrer's opaque user id, so the reward can be credited on the referee's first
 * delivered box; no phone number or token goes into the link.
 * @param {string} societyId
 * @param {string | null | undefined} referrerId
 */
export function inviteUrl(societyId, referrerId) {
  const url = `${PUBLIC_WEB_URL}/s/${encodeURIComponent(societyId)}`;
  return referrerId ? `${url}?ref=${encodeURIComponent(referrerId)}` : url;
}

/**
 * Invite neighbours to a society: the phone's share sheet (Web Share), or the link copied with a
 * toast where sharing is not available. Closing the share sheet is not an error.
 * @param {{ societyId: string, text: string, copied: string }} invite
 */
export async function shareInvite({ societyId, text, copied }) {
  const url = inviteUrl(societyId, useSession.getState().user?.id);
  try {
    if (navigator.share) await navigator.share({ title: "GharBasket", text, url });
    else {
      await navigator.clipboard.writeText(`${text} ${url}`);
      toast(copied);
    }
  } catch {
    // The person closed the share sheet.
  }
}
