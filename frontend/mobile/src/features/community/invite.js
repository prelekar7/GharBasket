import { toast } from "@gharbasket/ui";

/**
 * Invite neighbours to a society: the phone's share sheet (Web Share), or the link copied with a
 * toast where sharing is not available. Closing the share sheet is not an error.
 * @param {{ societyId: string, text: string, copied: string }} invite
 */
export async function shareInvite({ societyId, text, copied }) {
  const url = `${location.origin}/s/${societyId}`;
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
