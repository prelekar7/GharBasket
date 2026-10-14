/**
 * Shrinks a camera photo to a small JPEG data URL (longest side 1024 px, about 100 KB), so a
 * handover photo can wait in the offline outbox with the rest of the handover and be sent later.
 * @param {File} file
 * @returns {Promise<string>}
 */
export async function smallJpeg(file, max = 1024, quality = 0.6) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return canvas.toDataURL("image/jpeg", quality);
}
