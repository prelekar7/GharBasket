// QR scanning in the Android app: ML Kit's `scan()` (ADR-0031). Google's own scanner screen runs in
// Play services, so the app asks for no camera permission and needs no camera code of its own.
import { withPlugin } from "@/shared/native/platform.js";

/** Longest the first-use download of Google's scanner module may take before we give up. */
export const INSTALL_MAX_MS = 120_000;

/**
 * The scanner cannot be used on this phone (no Play services, module download failed or was
 * cancelled, or the scan itself failed). The screen shows "Type the code instead".
 */
export class ScannerUnavailableError extends Error {
  /** @param {string} [reason] */
  constructor(reason = "unavailable") {
    super(`scanner unavailable: ${reason}`);
    this.name = "ScannerUnavailableError";
    this.code = "scanner.unavailable";
  }
}

/** The message of an Error or of a plugin's plain error object. @param {any} e */
const messageOf = (e) => String(e?.message ?? e ?? "");

/** The plugin rejects "scan canceled." when the person closes the scanner. @param {unknown} e */
const isCancel = (e) => /cancel/i.test(messageOf(e));

/** The plugin says the module is already there when a download races another; that is fine. @param {unknown} e */
const isAlreadyInstalled = (e) => /already installed/i.test(messageOf(e));

/**
 * First use: Google downloads its scanner module in the background (a few MB). Resolves when it is
 * installed, rejects when the download fails, is cancelled or takes too long.
 * @param {typeof import("@capacitor-mlkit/barcode-scanning")} m
 * @returns {Promise<void>}
 */
async function installModule(m) {
  const { BarcodeScanner, GoogleBarcodeScannerModuleInstallState: State } = m;
  /** @type {{ remove: () => Promise<void> } | undefined} */
  let handle;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let timer;
  try {
    await new Promise((resolve, reject) => {
      timer = setTimeout(
        () => reject(new ScannerUnavailableError("install timeout")),
        INSTALL_MAX_MS,
      );
      BarcodeScanner.addListener("googleBarcodeScannerModuleInstallProgress", ({ state }) => {
        if (state === State.COMPLETED) resolve(undefined);
        else if (state === State.FAILED || state === State.CANCELED)
          reject(new ScannerUnavailableError("install failed"));
      })
        .then((h) => {
          handle = h;
          return BarcodeScanner.installGoogleBarcodeScannerModule();
        })
        .catch((e) => (isAlreadyInstalled(e) ? resolve(undefined) : reject(e)));
    });
  } finally {
    clearTimeout(timer);
    await handle?.remove().catch(() => {});
  }
}

/**
 * Opens Google's scanner and returns the raw text of the first QR code, or null when the person
 * closes it (and on the web, which falls back to typing). Throws ScannerUnavailableError when this
 * phone cannot scan.
 * @param {{ onPreparing?: (preparing: boolean) => void }} [options] `onPreparing(true)` while the
 *   scanner module downloads for the first time (show "Getting the scanner ready…")
 * @returns {Promise<string | null>}
 */
export async function scanQr({ onPreparing } = {}) {
  const raw = await withPlugin(
    () => import("@capacitor-mlkit/barcode-scanning"),
    async (m) => {
      const { BarcodeScanner, BarcodeFormat } = m;
      try {
        if (!(await BarcodeScanner.isSupported()).supported)
          throw new ScannerUnavailableError("unsupported");
        if (!(await BarcodeScanner.isGoogleBarcodeScannerModuleAvailable()).available) {
          onPreparing?.(true);
          try {
            await installModule(m);
          } finally {
            onPreparing?.(false);
          }
        }
        const { barcodes } = await BarcodeScanner.scan({ formats: [BarcodeFormat.QrCode] });
        const first = barcodes[0];
        return first ? (first.rawValue ?? first.displayValue ?? null) : null;
      } catch (e) {
        if (e instanceof ScannerUnavailableError) throw e;
        if (isCancel(e)) return null;
        throw new ScannerUnavailableError(messageOf(e) || "scan failed");
      }
    },
    () => null,
  );
  return raw || null;
}

/**
 * Android may kill the app while Google's scanner is open; the promise then never resolves and the
 * result arrives as `appRestoredResult` on the App plugin (an error when the process died, so the
 * code is lost). Listen while a scan screen is up. `onResult(code)` gets the QR text (or null
 * when the scan was lost or cancelled, with `lost` true when it should be done again).
 * @param {(result: { code: string | null, lost: boolean }) => void} onResult
 * @returns {() => void} stop listening
 */
export function onRestoredScan(onResult) {
  let stopped = false;
  let stop = () => {};
  withPlugin(
    () => import("@capacitor/app"),
    async ({ App }) => {
      const handle = await App.addListener("appRestoredResult", (event) => {
        if (event.pluginId !== "BarcodeScanner" || event.methodName !== "scan") return;
        const first = event.success ? event.data?.barcodes?.[0] : undefined;
        const code = first?.rawValue ?? first?.displayValue ?? null;
        onResult({ code, lost: !code && !(event.error && isCancel(event.error)) });
      });
      if (stopped) handle.remove();
      else stop = () => handle.remove();
    },
  ).catch(() => {}); // no restore listener is not worth an error screen
  return () => {
    stopped = true;
    stop();
  };
}
