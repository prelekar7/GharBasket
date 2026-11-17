import { afterEach, describe, expect, it, vi } from "vitest";

const scanQr = vi.fn(async (/** @type {any} */ _o) => /** @type {string | null} */ ("  GB1:x \n"));
vi.mock("@/shared/native/scanner.js", () => ({ scanQr }));

/** Loads scan.js with the mock API on or off. @param {boolean} mock */
async function load(mock) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: false, MOCK: mock, API_BASE: "" }));
  return import("./scan.js");
}

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  vi.doUnmock("@/lib/env.js");
});

describe("scanCode", () => {
  it("on mocks 'scans' the code the screen expects, and never opens the scanner", async () => {
    vi.useFakeTimers();
    const { scanCode } = await load(true);
    const p = scanCode({ expected: "B-214" });
    await vi.advanceTimersByTimeAsync(400);
    expect(await p).toBe("B-214");
    expect(await scanCode()).toBeNull();
    expect(scanQr).not.toHaveBeenCalled();
  });

  it("otherwise reads the QR with the scanner and returns the trimmed text", async () => {
    const { scanCode } = await load(false);
    const onPreparing = vi.fn();
    expect(await scanCode({ expected: "B-214", onPreparing })).toBe("GB1:x");
    expect(scanQr).toHaveBeenCalledWith({ onPreparing });
  });

  it("answers null when the scanner was closed or there is none", async () => {
    scanQr.mockResolvedValueOnce(null);
    const { scanCode } = await load(false);
    expect(await scanCode()).toBeNull();
  });
});

describe("parseScan", () => {
  const hex = "0199a0b2c3d44e5f8a9b0c1d2e3f4a5b";

  it("reads a bag, a kitchen pickup and the printed short codes", async () => {
    const { parseScan } = await load(false);
    expect(parseScan(`GB1:${hex}`)).toEqual({ kind: "bag", value: `GB1:${hex}` });
    expect(parseScan(`GB1A:${hex}`)).toEqual({ kind: "pickup", value: `GB1A:${hex}` });
    expect(parseScan("B-214")).toEqual({ kind: "short", value: "B-214" });
    expect(parseScan("S-1")).toEqual({ kind: "short", value: "S-1" });
    expect(parseScan("T-12")).toEqual({ kind: "short", value: "T-12" });
  });

  it("is forgiving about hex case, whitespace and loosely typed short codes", async () => {
    const { parseScan } = await load(false);
    expect(parseScan(`  GB1:${hex.toUpperCase()}\n`)).toEqual({ kind: "bag", value: `GB1:${hex}` });
    expect(parseScan(` b 214 `)).toEqual({ kind: "short", value: "B-214" });
    expect(parseScan("t12")).toEqual({ kind: "short", value: "T-12" });
  });

  it("rejects anything else", async () => {
    const { parseScan } = await load(false);
    for (const bad of [
      "",
      "   ",
      "hello",
      "https://gharbasket.co.in/b/214",
      `GB2:${hex}`,
      `gb1:${hex}`,
      `GB1B:${hex}`,
      `GB1:${hex.slice(1)}`,
      `GB1:${hex}0`,
      `GB1:${hex.replace("0", "g")}`,
      `GB1: ${hex}`,
      `XGB1:${hex}`,
      "B-",
      "214",
      "B-2x4",
    ])
      expect(parseScan(bad), JSON.stringify(bad)).toBeNull();
  });
});
