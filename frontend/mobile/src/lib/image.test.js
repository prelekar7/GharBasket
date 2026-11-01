import { describe, expect, it, vi } from "vitest";
import { downscaleImage, fitWithin, isResizable, MAX_BYTES, needsResize } from "./image.js";

const file = (/** @type {string} */ type, /** @type {number} */ size, name = "IMG_1.jpeg") =>
  new File([new Uint8Array(size)], name, { type });
const encoded = (/** @type {number} */ size, width = 1600, height = 1200) => ({
  blob: new Blob([new Uint8Array(size)], { type: "image/jpeg" }),
  dimensions: { width, height },
});

describe("needsResize", () => {
  it("shrinks above the byte limit or past the longest side, not otherwise", () => {
    expect(needsResize({ size: MAX_BYTES + 1 })).toBe(true);
    expect(needsResize({ size: 1000 }, { width: 4000, height: 3000 })).toBe(true);
    expect(needsResize({ size: 1000 }, { width: 3000, height: 4000 })).toBe(true);
    expect(needsResize({ size: 1000 }, { width: 1600, height: 1200 })).toBe(false);
    expect(needsResize({ size: MAX_BYTES })).toBe(false);
  });
});

describe("isResizable", () => {
  it("only touches jpeg, png and webp", () => {
    for (const t of ["image/jpeg", "image/png", "image/webp"])
      expect(isResizable({ type: t })).toBe(true);
    for (const t of ["application/pdf", "image/gif", "image/heic", ""])
      expect(isResizable({ type: t })).toBe(false);
  });
});

describe("fitWithin", () => {
  it("keeps the shape and never enlarges", () => {
    expect(fitWithin(4000, 3000)).toEqual({ width: 1600, height: 1200 });
    expect(fitWithin(3000, 4000)).toEqual({ width: 1200, height: 1600 });
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 });
  });
});

describe("downscaleImage", () => {
  it("leaves a PDF untouched without decoding it", async () => {
    const pdf = file("application/pdf", 5_000_000, "kyc.pdf");
    const encode = vi.fn();
    expect(await downscaleImage(pdf, encode)).toBe(pdf);
    expect(encode).not.toHaveBeenCalled();
  });

  it("re-encodes a 4.4 MB photo as a smaller JPEG, forced because of its size", async () => {
    const big = file("image/jpeg", 4_400_000);
    const encode = vi.fn(async () => encoded(300_000));
    const out = await downscaleImage(big, encode);
    expect(encode).toHaveBeenCalledWith(big, true);
    expect(out).not.toBe(big);
    expect(out.type).toBe("image/jpeg");
    expect(out.name).toBe("IMG_1.jpg");
    expect(out.size).toBe(300_000);
  });

  it("names a PNG's result .jpg", async () => {
    const out = await downscaleImage(file("image/png", 2_000_000, "scan.png"), async () =>
      encoded(200_000),
    );
    expect(out.name).toBe("scan.jpg");
  });

  it("checks a small photo's pixels, and keeps it when they are fine", async () => {
    const small = file("image/jpeg", 400_000);
    const encode = vi.fn(async () => ({ blob: small, dimensions: { width: 1280, height: 960 } }));
    expect(await downscaleImage(small, encode)).toBe(small);
    expect(encode).toHaveBeenCalledWith(small, false);
  });

  it("shrinks a small file with too many pixels", async () => {
    const small = file("image/jpeg", 900_000);
    // `dimensions` is the decoded original's size: 4000 px wide.
    const out = await downscaleImage(small, async () => encoded(500_000, 4000, 3000));
    expect(out).not.toBe(small);
    expect(out.size).toBe(500_000);
  });

  it("keeps the original when the re-encode would be bigger and the pixels are fine", async () => {
    const small = file("image/webp", 100_000);
    expect(await downscaleImage(small, async () => encoded(150_000, 1200, 900))).toBe(small);
  });

  it("falls back to the original when decoding fails or throws", async () => {
    const big = file("image/jpeg", 4_400_000);
    expect(await downscaleImage(big, async () => null)).toBe(big);
    expect(
      await downscaleImage(big, async () => {
        throw new Error("decode");
      }),
    ).toBe(big);
  });
});
