import { NATIVE } from "@/lib/env.js";
import { withPlugin } from "./platform.js";

/**
 * Shares text and a link: the Android share sheet in the app; on the web the Web Share sheet, or
 * the text copied to the clipboard where that is missing. Never throws.
 * @param {{ title: string, text: string, url: string }} content
 * @returns {Promise<"shared" | "copied" | "dismissed" | "failed">} "dismissed": the person closed the sheet
 */
export async function shareContent({ title, text, url }) {
  try {
    if (NATIVE) {
      await withPlugin(
        () => import("@capacitor/share"),
        async ({ Share }) => {
          await Share.share({ title, text, url, dialogTitle: title });
        },
      );
      return "shared";
    }
    if (navigator.share) {
      await navigator.share({ title, text, url });
      return "shared";
    }
    await navigator.clipboard.writeText(`${text} ${url}`);
    return "copied";
  } catch (e) {
    return isDismissal(e) ? "dismissed" : "failed";
  }
}

/** Closing a share sheet rejects: AbortError on the web, "Share canceled" in the app. @param {unknown} e */
function isDismissal(e) {
  const err = /** @type {{ name?: string, message?: string }} */ (e ?? {});
  return err.name === "AbortError" || /cancel/i.test(err.message ?? "");
}
