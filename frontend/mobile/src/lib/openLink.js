import { NATIVE } from "@/lib/env.js";
import { openExternal } from "@/shared/native/links.js";

/**
 * Opens a link that must first be asked of the API (a short-lived signed URL to a PDF).
 *
 * On the web a new tab is opened during the tap, so a popup blocker allows it, and the link fills
 * it in once the API has answered; when no tab can be opened the app itself goes to the link. On
 * failure the empty tab is closed and the error is thrown for the caller to show.
 *
 * In the Android app a blank tab cannot be filled in later, so the link is awaited first and then
 * handed to the phone (a PDF viewer or the browser). When nothing can open it the error is thrown.
 * @param {() => Promise<string>} getUrl
 */
export async function openLink(getUrl) {
  if (NATIVE) {
    const url = await getUrl();
    if (!(await openExternal(url))) throw new Error("Could not open the link");
    return;
  }
  const tab = window.open("", "_blank");
  try {
    const url = await getUrl();
    if (tab) {
      tab.opener = null;
      tab.location.href = url;
    } else window.location.assign(url);
  } catch (e) {
    tab?.close();
    throw e;
  }
}
