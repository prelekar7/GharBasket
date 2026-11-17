import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const State = { PENDING: 1, DOWNLOADING: 2, CANCELED: 3, COMPLETED: 4, FAILED: 5 };
/** @type {((e: { state: number }) => void) | undefined} */
let progress;
const remove = vi.fn(async () => {});
const scanner = {
  isSupported: vi.fn(async () => ({ supported: true })),
  isGoogleBarcodeScannerModuleAvailable: vi.fn(async () => ({ available: true })),
  installGoogleBarcodeScannerModule: vi.fn(async () => {}),
  addListener: vi.fn(async (/** @type {string} */ _name, /** @type {any} */ fn) => {
    progress = fn;
    return { remove };
  }),
  scan: vi.fn(async (/** @type {any} */ _o) => ({ barcodes: [{ rawValue: "GB1:abc" }] })),
};
vi.mock("@capacitor-mlkit/barcode-scanning", () => ({
  BarcodeScanner: scanner,
  BarcodeFormat: { QrCode: "QR_CODE" },
  GoogleBarcodeScannerModuleInstallState: State,
}));
/** @type {((e: any) => void) | undefined} */
let restored;
const appRemove = vi.fn();
vi.mock("@capacitor/app", () => ({
  App: {
    addListener: vi.fn(async (/** @type {string} */ _n, /** @type {any} */ fn) => {
      restored = fn;
      return { remove: appRemove };
    }),
  },
}));

/** Loads scanner.js with env.js reporting a native or a web build. @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./scanner.js");
}

beforeEach(() => {
  progress = undefined;
  restored = undefined;
});
afterEach(() => {
  vi.clearAllMocks();
  vi.doUnmock("@/lib/env.js");
  scanner.isSupported.mockResolvedValue({ supported: true });
  scanner.isGoogleBarcodeScannerModuleAvailable.mockResolvedValue({ available: true });
  scanner.scan.mockResolvedValue({ barcodes: [{ rawValue: "GB1:abc" }] });
});

describe("scanQr", () => {
  it("reads only QR codes and returns the raw value", async () => {
    const { scanQr } = await load(true);
    expect(await scanQr()).toBe("GB1:abc");
    expect(scanner.scan).toHaveBeenCalledWith({ formats: ["QR_CODE"] });
    expect(scanner.installGoogleBarcodeScannerModule).not.toHaveBeenCalled();
  });

  it("answers null on the web without loading the plugin", async () => {
    const { scanQr } = await load(false);
    expect(await scanQr()).toBeNull();
    expect(scanner.scan).not.toHaveBeenCalled();
  });

  it("returns null when the person closes the scanner", async () => {
    scanner.scan.mockRejectedValue(new Error("scan canceled."));
    const { scanQr } = await load(true);
    expect(await scanQr()).toBeNull();
  });

  it("returns null when no code was read", async () => {
    scanner.scan.mockResolvedValue({ barcodes: [] });
    const { scanQr } = await load(true);
    expect(await scanQr()).toBeNull();
  });

  it("installs Google's module on first use, reporting progress, then scans", async () => {
    scanner.isGoogleBarcodeScannerModuleAvailable.mockResolvedValue({ available: false });
    scanner.installGoogleBarcodeScannerModule.mockImplementation(async () => {
      queueMicrotask(() => progress?.({ state: State.DOWNLOADING }));
      queueMicrotask(() => progress?.({ state: State.COMPLETED }));
    });
    const { scanQr } = await load(true);
    const onPreparing = vi.fn();
    expect(await scanQr({ onPreparing })).toBe("GB1:abc");
    expect(onPreparing.mock.calls).toEqual([[true], [false]]);
    expect(scanner.installGoogleBarcodeScannerModule).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalled();
  });

  it("throws the typed error when the module download fails", async () => {
    scanner.isGoogleBarcodeScannerModuleAvailable.mockResolvedValue({ available: false });
    scanner.installGoogleBarcodeScannerModule.mockImplementation(async () => {
      queueMicrotask(() => progress?.({ state: State.FAILED }));
    });
    const { scanQr, ScannerUnavailableError } = await load(true);
    const onPreparing = vi.fn();
    await expect(scanQr({ onPreparing })).rejects.toBeInstanceOf(ScannerUnavailableError);
    expect(onPreparing).toHaveBeenLastCalledWith(false);
    expect(scanner.scan).not.toHaveBeenCalled();
  });

  it("throws the typed error when the phone has no scanner support or the scan fails", async () => {
    scanner.isSupported.mockResolvedValueOnce({ supported: false });
    const { scanQr, ScannerUnavailableError } = await load(true);
    await expect(scanQr()).rejects.toBeInstanceOf(ScannerUnavailableError);
    scanner.scan.mockRejectedValue(new Error("The scan failed."));
    await expect(scanQr()).rejects.toBeInstanceOf(ScannerUnavailableError);
  });
});

describe("onRestoredScan", () => {
  it("delivers the code of a scan restored after Android recreated the app", async () => {
    const { onRestoredScan } = await load(true);
    const got = vi.fn();
    const stop = onRestoredScan(got);
    await vi.waitFor(() => expect(restored).toBeDefined());
    restored?.({ pluginId: "Camera", methodName: "getPhoto", success: true });
    restored?.({
      pluginId: "BarcodeScanner",
      methodName: "scan",
      success: true,
      data: { barcodes: [{ rawValue: "GB1A:x" }] },
    });
    restored?.({
      pluginId: "BarcodeScanner",
      methodName: "scan",
      success: false,
      error: { message: "The scan result was lost because the app process was terminated." },
    });
    expect(got.mock.calls).toEqual([
      [{ code: "GB1A:x", lost: false }],
      [{ code: null, lost: true }],
    ]);
    stop();
    expect(appRemove).toHaveBeenCalled();
  });

  it("listens to nothing on the web", async () => {
    const { onRestoredScan } = await load(false);
    onRestoredScan(vi.fn())();
    expect(restored).toBeUndefined();
  });
});
