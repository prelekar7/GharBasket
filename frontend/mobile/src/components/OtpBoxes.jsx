import { useId, useState } from "react";

/**
 * Six code boxes over one real input, so SMS autofill, paste and screen readers all see a single
 * field while it looks like the design's boxes (Hf03).
 * @param {{ legend: string, value: string, onChange: (code: string) => void, error?: string | null }} props
 */
export function OtpBoxes({ legend, value, onChange, error }) {
  const id = useId();
  const [focused, setFocused] = useState(false);
  return (
    <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
      <legend className="mb-2.5 p-0 text-sm leading-5 text-fg-secondary">
        <label htmlFor={id}>{legend}</label>
      </legend>
      <div className="relative flex justify-between gap-2">
        {Array.from({ length: 6 }, (_, i) => {
          const current = focused && i === Math.min(value.length, 5);
          return (
            <span
              // biome-ignore lint/suspicious/noArrayIndexKey: six fixed positions
              key={i}
              aria-hidden
              className={`grid h-14 w-[46px] place-items-center rounded-sm bg-surface text-[22px] font-semibold tabular-nums ${
                error
                  ? "border-2 border-danger"
                  : current
                    ? "border-2 border-primary shadow-[0_0_0_3px_var(--color-primary-soft)]"
                    : "border border-line-strong"
              }`}
            >
              {value[i] ?? ""}
            </span>
          );
        })}
        <input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          // biome-ignore lint/a11y/noAutofocus: the only thing to do on this screen is type the code
          autoFocus
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          className="absolute inset-0 h-full w-full cursor-text opacity-0"
        />
      </div>
      {error && (
        <p id={`${id}-error`} role="alert" className="text-sm font-semibold text-danger">
          {error}
        </p>
      )}
    </fieldset>
  );
}
