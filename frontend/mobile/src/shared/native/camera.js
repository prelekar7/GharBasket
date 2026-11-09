// Photos in the Android app through @capacitor/camera 8.x (ADR-0033). It uses the new `takePhoto`
// and `chooseFromGallery` calls; `getPhoto` is deprecated. Neither needs a permission here: the
// manifest has no CAMERA, so the plugin hands the shot to the system camera app (an
// ACTION_IMAGE_CAPTURE intent) and the gallery to the system picker, and nothing is saved to the
// gallery. The phone (a Rs 8,000, 3 GB one) shrinks the picture natively, and JavaScript never
// decodes it: the file is read as a Blob and goes to storage as it is.
import { withPlugin } from "./platform.js";

/** Longest side of a photo we keep, in pixels. About 1 MB at quality 70, well under the 5 MB limit. */
export const PHOTO_MAX_EDGE = 1600;
export const PHOTO_QUALITY = 70;

/** Types the API takes for photos (platform/files.py); the picker may hand back others, e.g. HEIC. */
const KEPT_TYPES = ["image/jpeg", "image/png", "image/webp"];
/** OS-PLUG-CAMR-0006: the photo was cancelled; OS-PLUG-CAMR-0020: the gallery pick was cancelled. */
const CANCEL_CODES = ["OS-PLUG-CAMR-0006", "OS-PLUG-CAMR-0020"];

/** @typedef {{ blob: Blob, contentType: string, name: string }} TakenPhoto */

/** Pressing back or "cancel" in the camera or the picker is not an error. @param {unknown} e */
function isCancel(e) {
  const { code, message } = /** @type {{ code?: unknown, message?: unknown }} */ (e ?? {});
  return (
    CANCEL_CODES.includes(String(code)) ||
    /cancel|no image picked|no picture taken/i.test(String(message ?? ""))
  );
}

/**
 * Asks for one photo. In the Android app it opens the camera (`"camera"`, the default) or the
 * gallery (`"gallery"`) and answers with the picture as a Blob, or `null` if the person backed
 * out. On the web it answers `null` at once and opens nothing: the screen keeps its own
 * `<input type="file">` there. Any other failure throws.
 * @param {{ source?: "camera" | "gallery" }} [options]
 * @returns {Promise<TakenPhoto | null>}
 */
export async function takePhoto({ source = "camera" } = {}) {
  const media = await withPlugin(
    () => import("@capacitor/camera"),
    async ({ Camera, MediaTypeSelection }) => {
      const sizes = {
        quality: PHOTO_QUALITY,
        targetWidth: PHOTO_MAX_EDGE,
        targetHeight: PHOTO_MAX_EDGE,
        correctOrientation: true,
      };
      try {
        if (source === "gallery") {
          const { results } = await Camera.chooseFromGallery({
            ...sizes,
            mediaType: MediaTypeSelection.Photo,
            allowMultipleSelection: false,
          });
          return results[0] ?? null;
        }
        return await Camera.takePhoto({ ...sizes, saveToGallery: false });
      } catch (e) {
        if (isCancel(e)) return null;
        throw e;
      }
    },
    async () => null,
  );
  if (!media?.webPath) return null;
  const read = await fetch(media.webPath);
  if (!read.ok) throw new Error(`photo ${read.status}`);
  const body = await read.blob();
  // The WebView names the type from the file's extension; keep it only if the API takes it.
  const contentType = KEPT_TYPES.includes(body.type) ? body.type : "image/jpeg";
  const ext = contentType === "image/jpeg" ? "jpg" : contentType.slice(6);
  return {
    // A new Blob over the same bytes (no copy) so the type is always the one we send.
    blob: new Blob([body], { type: contentType }),
    contentType,
    name: `gharbasket-${Date.now()}.${ext}`,
  };
}

/** The photo as a File, for screens that keep a list of Files. @param {TakenPhoto} photo */
export const photoFile = ({ blob, contentType, name }) =>
  new File([blob], name, { type: contentType });
