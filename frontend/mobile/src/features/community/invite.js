import { toast } from "@gharbasket/ui";

/**
 * Invite neighbours to a society: the phone's share sheet (Web Share), or the link copied with a
 * toast where sharing is not available. Closing the share sheet is not an error.
 * @param {{ societyId: string, text: string, copied: string }} invite
 */
export async function shareInvite({ societyId, text, copied }) {
  const url = inviteLink(societyId);
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

/** The society's landing page that an invite points to (M9 `/s/<society>`). @param {string} societyId */
export const inviteLink = (societyId) => `${location.origin}/s/${societyId}`;

/** WhatsApp with the message ready to send (wa.me works in the app and on the web). @param {string} text */
export const whatsappShare = (text) =>
  window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener");

/** A chat with one person on WhatsApp, e.g. the Society Captain. @param {string} phoneE164 */
export const whatsappChat = (phoneE164) =>
  window.open(`https://wa.me/${phoneE164.replace(/\D/g, "")}`, "_blank", "noopener");
