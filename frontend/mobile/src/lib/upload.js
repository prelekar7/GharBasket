import { request } from "@/lib/api.js";
import { MOCK } from "@/lib/env.js";
import { downscaleImage } from "@/lib/image.js";

/** What a file is for (M0 §3.1): decides the allowed types, the size limit and how long it is kept. @typedef {"order_issue_photo" | "donation_photo" | "kyc_document" | "delivery_photo" | "handoff_photo"} UploadPurpose */
// "delivery_photo" is M7's purpose (proof, undelivered and damage photos); M0 §3.1 does not list it yet.

const MB = 1024 * 1024;
/** The size the API accepts per purpose (backend platform/files.py PURPOSES). @type {Record<UploadPurpose, number>} */
export const UPLOAD_LIMITS = {
  order_issue_photo: 5 * MB,
  donation_photo: 5 * MB,
  handoff_photo: 5 * MB,
  delivery_photo: 5 * MB,
  kyc_document: 10 * MB,
};

/** A file over its purpose's limit; thrown before anything is sent so the screen can say so. */
export class UploadTooLargeError extends Error {
  /** @param {UploadPurpose} purpose @param {number} sizeBytes */
  constructor(purpose, sizeBytes) {
    super(`upload too large: ${sizeBytes} bytes for ${purpose}`);
    this.name = "UploadTooLargeError";
    this.code = "upload.too_large";
    this.purpose = purpose;
    this.sizeBytes = sizeBytes;
    /** The limit in whole MB, for the message. */
    this.limitMb = UPLOAD_LIMITS[purpose] / MB;
  }
}

/**
 * Uploads one file straight to private storage and returns its key (POST /v1/files/presign, M0;
 * handbook 10: the API keeps only the key and metadata). The PUT carries exactly the headers the
 * presign answers with. The default purpose is a photo of a problem with an order. A big photo is
 * shrunk first (lib/image.js), because some cameras ignore the size the app asks for; PDFs go as is.
 * A file still over the purpose's limit throws UploadTooLargeError before the presign call. A camera
 * photo is a Blob whose `type` is already set (native/camera.js), so it goes the same way as a File.
 * @param {File | Blob} original @param {UploadPurpose} [purpose]
 */
export async function uploadFile(original, purpose = "order_issue_photo") {
  const file = await downscaleImage(original);
  if (file.size > UPLOAD_LIMITS[purpose]) throw new UploadTooLargeError(purpose, file.size);
  const { url, key, headers } =
    /** @type {{ url: string, key: string, headers: Record<string, string> }} */ (
      await request("/v1/files/presign", {
        method: "POST",
        body: { purpose, content_type: file.type, size_bytes: file.size },
      })
    );
  // The browser sets Content-Length from the body itself and refuses it as a header.
  const { "Content-Length": _length, ...send } = headers;
  const put = new Request(url, { method: "PUT", body: file, headers: send });
  // Same switch as lib/api.js: the fake storage lives in the mocks and never keeps the bytes.
  // `DEV` inline (as well as inside MOCK) lets the bundler drop the mocks from every build.
  const res =
    import.meta.env.DEV && MOCK
      ? await (await import("@/mocks/index.js")).mockFetch(put)
      : await fetch(put);
  if (!res.ok) throw new Error(`upload ${res.status}`);
  return key;
}
