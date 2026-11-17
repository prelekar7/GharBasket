import { NATIVE } from "@/lib/env.js";
import { withPlugin } from "./platform.js";

/**
 * Keeps the status and navigation bar icons readable in dark mode. capacitor.config.json sets dark
 * icons for the light theme; the theme follows the phone's setting (tokens.css), so when the phone
 * is dark the icons turn light. Does nothing on the web.
 * @returns {() => void} stops following the setting
 */
export function startSystemBars() {
  if (!NATIVE) return () => {};
  const query = matchMedia("(prefers-color-scheme: dark)");
  const apply = () =>
    withPlugin(
      () => import("@capacitor/core"),
      ({ SystemBars, SystemBarsStyle }) =>
        SystemBars.setStyle({
          style: query.matches ? SystemBarsStyle.Dark : SystemBarsStyle.Light,
        }),
    ).catch(() => {});
  apply();
  query.addEventListener("change", apply);
  return () => query.removeEventListener("change", apply);
}
