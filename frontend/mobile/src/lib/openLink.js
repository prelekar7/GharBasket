/**
 * Opens a link that must first be asked of the API (a short-lived signed URL to a PDF) in a new tab.
 * The tab is opened during the tap, so a popup blocker allows it, and the link fills it in once the
 * API has answered; when no tab can be opened the app itself goes to the link. On failure the empty
 * tab is closed and the error is thrown for the caller to show.
 * @param {() => Promise<string>} getUrl
 */
export async function openLink(getUrl) {
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
