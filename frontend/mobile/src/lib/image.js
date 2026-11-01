// Shrinks a big photo before it is uploaded. Some Android cameras ignore the size the app asks for
// (a vivo phone returned a 4.4 MB JPEG for "at most 1600 px, quality 70"), and a field role on a
// weak connection then waits minutes for one proof photo. Done in the web layer so it works the
// same for the browser, the PWA and the Capacitor shell.

/** Photos above this many bytes are always re-encoded. */
export const MAX_BYTES = 1.5 * 1024 * 1024;
/** The longest side after resizing, in pixels. */
export const MAX_SIDE = 1600;
/** JPEG quality of the re-encoded photo. */
export const QUALITY = 0.7;

const IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

/**
 * Is this file a photo worth looking at? PDFs and everything else are left untouched. Pure.
 * @param {{ type: string }} file
 */
export const isResizable = (file) => IMAGE_TYPES.has(file.type);

/**
 * Pure decision: a photo bigger than the byte limit, or longer than the side limit, is shrunk.
 * @param {{ size: number }} file @param {{ width: number, height: number }} [dimensions]
 */
export const needsResize = (file, dimensions) =>
  file.size > MAX_BYTES ||
  (!!dimensions && Math.max(dimensions.width, dimensions.height) > MAX_SIDE);

/** The size that fits `maxSide`, keeping the shape. Pure. @param {number} width @param {number} height @param {number} [maxSide] */
export function fitWithin(width, height, maxSide = MAX_SIDE) {
  const scale = Math.min(1, maxSide / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/**
 * The canvas part, kept apart so tests can replace it (jsdom has no canvas): decodes the photo
 * (honouring its EXIF orientation), draws it at most MAX_SIDE px on the long side and encodes a
 * JPEG. Returns null when the photo cannot be decoded. `dimensions` is the decoded size, so the
 * caller can tell whether the photo was too long even when its file was small.
 * @param {Blob} file @param {boolean} force re-encode even when the pixel size is already fine
 * @returns {Promise<{ blob: Blob, dimensions: { width: number, height: number } } | null>}
 */
export async function encodeJpeg(file, force) {
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return null;
  }
  try {
    const dimensions = { width: bitmap.width, height: bitmap.height };
    if (!force && !needsResize(file, dimensions)) return { blob: file, dimensions };
    const { width, height } = fitWithin(bitmap.width, bitmap.height);
    const canvas =
      typeof OffscreenCanvas === "function"
        ? new OffscreenCanvas(width, height)
        : Object.assign(document.createElement("canvas"), { width, height });
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    // JPEG has no transparency: a PNG's clear pixels would turn black.
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(bitmap, 0, 0, width, height);
    const blob =
      canvas instanceof HTMLCanvasElement
        ? await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", QUALITY))
        : await /** @type {OffscreenCanvas} */ (canvas).convertToBlob({
            type: "image/jpeg",
            quality: QUALITY,
          });
    return blob ? { blob, dimensions } : null;
  } finally {
    bitmap.close();
  }
}

/**
 * Returns the file to upload: the original when it is not a photo, is small enough, or cannot be
 * decoded; otherwise a JPEG at most MAX_SIDE px long. Never makes the upload bigger or throws.
 * @param {File} file
 * @param {(file: Blob, force: boolean) => ReturnType<typeof encodeJpeg>} [encode] the canvas part
 * @returns {Promise<File>}
 */
export async function downscaleImage(file, encode = encodeJpeg) {
  if (!isResizable(file)) return file;
  try {
    const out = await encode(file, file.size > MAX_BYTES);
    if (!out || out.blob === file) return file;
    // A small photo that was only re-encoded to fit the pixels must not come out bigger.
    if (out.blob.size >= file.size && !needsResize({ size: 0 }, out.dimensions)) return file;
    const name = file.name.replace(/\.[^./\\]+$/, "") || "photo";
    return new File([out.blob], `${name}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
  } catch {
    return file;
  }
}
