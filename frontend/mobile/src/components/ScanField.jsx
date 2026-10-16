import { ScanButton } from "@gharbasket/ui";
import { useState } from "react";
import { scanCode } from "@/lib/scan.js";

/**
 * The big scan button with "Type the code instead" under it. Every scan in the field roles has
 * this typed fallback (field-role canvas: torn labels, no camera, bad light).
 * @param {{ label: string, manualLabel: string, onCode: (code: string) => void, onManual: () => void,
 *   expected?: string, state?: "idle" | "success" | "error", successLabel?: string, errorLabel?: string }} props
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
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-col">
      <ScanButton
        state={state}
        label={label}
        successLabel={successLabel}
        errorLabel={errorLabel}
        onPress={async () => {
          if (busy) return;
          setBusy(true);
          const code = await scanCode({ expected }).finally(() => setBusy(false));
          if (code) onCode(code);
          else onManual();
        }}
      />
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
