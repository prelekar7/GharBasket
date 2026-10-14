import { request } from "@/lib/api.js";
import { MOCK } from "@/lib/env.js";

/** What a file is for (M0 §3.1): decides the allowed types, the size limit and how long it is kept. @typedef {"order_issue_photo" | "donation_photo" | "kyc_document" | "delivery_photo"} UploadPurpose */
// "delivery_photo" is M7's purpose (proof, undelivered and damage photos); M0 §3.1 does not list it yet.

/**
 * Uploads one file straight to private storage and returns its key (POST /v1/files/presign, M0;
 * handbook 10: the API keeps only the key and metadata). The PUT carries exactly the headers the
 * presign answers with. The default purpose is a photo of a problem with an order.
 * @param {File} file @param {UploadPurpose} [purpose]
 */
export async function uploadFile(file, purpose = "order_issue_photo") {
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
  const res = MOCK ? await (await import("@/mocks/index.js")).mockFetch(put) : await fetch(put);
  if (!res.ok) throw new Error(`upload ${res.status}`);
  return key;
}
