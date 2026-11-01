import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.fn();
vi.mock("@/lib/api.js", () => ({ request }));
vi.mock("@/lib/env.js", () => ({ NATIVE: false, MOCK: false, API_BASE: "" }));

const { UPLOAD_LIMITS, UploadTooLargeError, uploadFile } = await import("./upload.js");

const MB = 1024 * 1024;
/** A blob of `bytes` bytes. @param {number} bytes @param {string} [type] */
const blob = (bytes, type = "image/jpeg") => new Blob([new Uint8Array(bytes)], { type });

/** @type {import("vitest").Mock} */
let put;
beforeEach(() => {
  request.mockResolvedValue({
    url: "https://r2.example/obj?sig=1",
    key: "uploads/u/abc.jpg",
    headers: { "Content-Type": "image/jpeg", "Content-Length": "9", "x-amz-meta": "kept" },
  });
  put = vi.fn(async () => new Response(null, { status: 200 }));
  vi.stubGlobal("fetch", put);
});
afterEach(() => {
  request.mockReset();
  vi.unstubAllGlobals();
});

describe("uploadFile", () => {
  it("presigns with the blob's own type and size, then PUTs the bytes with the presigned headers", async () => {
    const photo = blob(1234);
    expect(await uploadFile(photo, "delivery_photo")).toBe("uploads/u/abc.jpg");
    expect(request).toHaveBeenCalledWith("/v1/files/presign", {
      method: "POST",
      body: { purpose: "delivery_photo", content_type: "image/jpeg", size_bytes: 1234 },
    });
    const sent = put.mock.calls[0][0];
    expect(sent.method).toBe("PUT");
    expect(sent.url).toBe("https://r2.example/obj?sig=1");
    expect(sent.headers.get("content-type")).toBe("image/jpeg");
    expect(sent.headers.get("x-amz-meta")).toBe("kept");
    expect((await sent.blob()).size).toBe(1234);
  });

  it("allows a file exactly at the limit", async () => {
    await uploadFile(blob(5 * MB), "donation_photo");
    expect(put).toHaveBeenCalledOnce();
  });

  it.each(
    /** @type {const} */ ([
      "order_issue_photo",
      "donation_photo",
      "handoff_photo",
      "delivery_photo",
    ]),
  )("refuses a photo over 5 MB for %s before the presign call", async (purpose) => {
    const err = await uploadFile(blob(5 * MB + 1), purpose).catch((e) => e);
    expect(err).toBeInstanceOf(UploadTooLargeError);
    expect(err).toMatchObject({ code: "upload.too_large", purpose, limitMb: 5 });
    expect(request).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
  });

  it("gives KYC documents 10 MB", async () => {
    expect(UPLOAD_LIMITS.kyc_document).toBe(10 * MB);
    await uploadFile(blob(6 * MB, "application/pdf"), "kyc_document");
    expect(put).toHaveBeenCalledOnce();
    const err = await uploadFile(blob(10 * MB + 1, "application/pdf"), "kyc_document").catch(
      (e) => e,
    );
    expect(err).toMatchObject({ code: "upload.too_large", limitMb: 10 });
  });

  it("still fails with the storage status when the PUT is refused", async () => {
    put.mockResolvedValue(new Response(null, { status: 403 }));
    await expect(uploadFile(blob(10), "order_issue_photo")).rejects.toThrow("upload 403");
  });
});
