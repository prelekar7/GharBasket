import { ScanButton } from "@gharbasket/ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { NATIVE } from "@/lib/env.js";
import { scanCode } from "@/lib/scan.js";
import { onRestoredScan, ScannerUnavailableError } from "@/shared/native/scanner.js";

/**
 * The big scan button with "Type the code instead" under it. Every scan in the field roles has
 * this typed fallback (field-role canvas: torn labels, no camera, bad light). In the Android app
 * the button opens Google's scanner; if the phone cannot scan, it says so and opens the typing.
 * @param {{ label: string, manualLabel: string, onCode: (code: string) => void, onManual: () => void,
 *   expected?: string, state?: "idle" | "success" | "error", successLabel?: string, errorLabel?: string,
 *   onScanned?: (code: string) => void }} props `onScanned`: a side effect when the camera read a
 *   code, before `onCode` (the haptic tap goes here once src/shared/native/haptics.js lands)
 */
export function ScanField({
  label,
  manualLabel,
  onCode,
  onManual,
  expected,
  state = "idle",
  successLabel,
  errorLabel,
  onScanned,
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [notice, setNotice] = useState("");
  // Latest callbacks for the restore listener, which is set up once.
  const latest = useRef({ onCode, onScanned });
  latest.current = { onCode, onScanned };

  // Android may have killed the app while the scanner was open: the code (or the loss) arrives here.
  useEffect(
    () =>
      onRestoredScan(({ code, lost }) => {
        if (code) {
          latest.current.onScanned?.(code);
          latest.current.onCode(code);
        } else if (lost) setNotice(t("scan.interrupted"));
      }),
    [t],
  );

  const press = async () => {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      const code = await scanCode({ expected, onPreparing: setPreparing });
      if (code) {
        onScanned?.(code);
        onCode(code);
      } else if (!NATIVE) onManual(); // in the app a closed scanner means "not now"
    } catch (e) {
      if (!(e instanceof ScannerUnavailableError)) throw e;
      setNotice(t("scan.unavailable"));
      onManual();
    } finally {
      setBusy(false);
      setPreparing(false);
    }
  };

  return (
    <div className="flex flex-col">
      <ScanButton
        state={state}
        label={preparing ? t("scan.preparing") : label}
        successLabel={successLabel}
        errorLabel={errorLabel}
        onPress={press}
      />
      {notice && (
        <p
          role="status"
          className="m-0 py-2 text-center text-[15px] leading-[22px] text-fg-secondary"
        >
          {notice}
        </p>
      )}
      <button
        type="button"
        onClick={onManual}
        className="min-h-12 w-full cursor-pointer border-0 bg-transparent text-[15px] font-semibold text-link underline underline-offset-[3px] outline-none focus-visible:shadow-focus"
      >
        {manualLabel}
      </button>
    </div>
  );
}
