import { request } from "@/lib/api.js";

const MOCK = import.meta.env.VITE_API_MOCK !== "false";

/**
 * Uploads one file straight to private storage and returns its key (POST /v1/files/presign, M0;
 * handbook 10: the API keeps only the key and metadata). `purpose` picks the bucket.
 * @param {File} file @param {"media" | "kyc"} [purpose]
 */
export async function uploadFile(file, purpose = "media") {
  const { url, key } = /** @type {{ url: string, key: string }} */ (
    await request("/v1/files/presign", {
      method: "POST",
      body: { content_type: file.type, size_bytes: file.size, purpose },
    })
  );
  const put = new Request(url, {
    method: "PUT",
    body: file,
    headers: { "content-type": file.type },
  });
  // Same switch as lib/api.js: the fake storage lives in the mocks and never keeps the bytes.
  const res = MOCK ? await (await import("@/mocks/index.js")).mockFetch(put) : await fetch(put);
  if (!res.ok) throw new Error(`upload ${res.status}`);
  return key;
}
