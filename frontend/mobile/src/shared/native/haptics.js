import { withPlugin } from "./platform.js";

/** A short buzz for the web, where the phone allows it. @param {number | number[]} pattern */
const vibrate = (pattern) => {
  navigator.vibrate?.(pattern);
};

/** Something worked (a scan was accepted). Never throws. */
export async function tapSuccess() {
  await withPlugin(
    () => import("@capacitor/haptics"),
    ({ Haptics, NotificationType }) => Haptics.notification({ type: NotificationType.Success }),
    () => vibrate(40),
  ).catch(() => {});
}

/** Something needs a second look (a wrong or repeated scan). Never throws. */
export async function tapWarning() {
  await withPlugin(
    () => import("@capacitor/haptics"),
    ({ Haptics, NotificationType }) => Haptics.notification({ type: NotificationType.Warning }),
    () => vibrate([60, 40, 60]),
  ).catch(() => {});
}
