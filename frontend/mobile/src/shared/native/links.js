// Opening things outside the app. On the phone the right app should open (WhatsApp, a UPI app,
// the dialler, Maps); other pages open in the in-app browser. The web keeps a plain new tab.
import { NATIVE } from "@/lib/env.js";
import { withPlugin } from "./platform.js";

const APP_HOSTS = new Set(["wa.me", "api.whatsapp.com", "maps.google.com", "maps.app.goo.gl"]);

/**
 * Links an installed app should take: other schemes (tel:, upi:, geo:, google.navigation:),
 * WhatsApp, Google Maps and PDFs.
 * @param {string} url
 */
export function wantsApp(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return true;
  if (APP_HOSTS.has(u.hostname)) return true;
  if (u.hostname.endsWith("google.com") && u.pathname.startsWith("/maps")) return true;
  return u.pathname.toLowerCase().endsWith(".pdf");
}

/**
 * Opens a link outside the app. Never throws.
 * @param {string} url
 * @returns {Promise<boolean>} false when nothing could open it
 */
export async function openExternal(url) {
  if (!NATIVE) {
    window.open(url, "_blank", "noopener");
    return true;
  }
  const inBrowser = async () => {
    await withPlugin(
      () => import("@capacitor/browser"),
      ({ Browser }) => Browser.open({ url }),
    );
    return true;
  };
  try {
    if (!wantsApp(url)) return await inBrowser();
    try {
      const opened = await withPlugin(
        () => import("@capacitor/app-launcher"),
        ({ AppLauncher }) => AppLauncher.openUrl({ url }).then((r) => r.completed),
      );
      if (opened) return true;
    } catch {
      // No app took it; a web address can still open in the browser below.
    }
    return /^https?:/i.test(url) ? await inBrowser() : false;
  } catch {
    return false;
  }
}
