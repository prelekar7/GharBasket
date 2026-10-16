"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

/**
 * "Your society" + "Find my society" of the landing page: a small form that opens the society's
 * invite page. Suggestions come from a native datalist, so there is little script to ship.
 * @param {{ societies: { slug: string, name: string, area: string }[] }} props
 */
export function SocietyFinder({ societies }) {
  const router = useRouter();
  const inputId = useId();
  const listId = useId();
  const [message, setMessage] = useState("");

  /** @param {import("react").FormEvent<HTMLFormElement>} event */
  function onSubmit(event) {
    event.preventDefault();
    const typed = String(new FormData(event.currentTarget).get("society") ?? "")
      .trim()
      .toLowerCase();
    // "Sunrise Towers, Kharghar" and "Sunrise" both find Sunrise Towers.
    const found = typed
      ? (societies.find((s) => `${s.name}, ${s.area}`.toLowerCase() === typed) ??
        societies.find((s) => typed.includes(s.name.toLowerCase())) ??
        societies.find((s) => s.name.toLowerCase().includes(typed.split(",")[0].trim())))
      : null;
    if (found) {
      setMessage("");
      router.push(`/s/${found.slug}`);
    } else {
      setMessage(
        typed
          ? "We could not find that society yet. Check the spelling, or ask for it in the app."
          : "Type your society’s name.",
      );
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex max-w-[520px] flex-col gap-2">
      <div className="flex flex-col items-stretch gap-3 min-[480px]:flex-row min-[480px]:items-end">
        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor={inputId} className="text-sm leading-5 font-semibold">
            Your society
          </label>
          <input
            id={inputId}
            name="society"
            list={listId}
            autoComplete="off"
            placeholder="e.g. Sunrise Towers, Kharghar"
            aria-describedby={message ? `${inputId}-msg` : undefined}
            aria-invalid={message ? true : undefined}
            className="min-h-12 w-full rounded-sm border border-line-strong bg-surface px-3 text-base text-fg outline-none placeholder:text-fg-secondary focus:shadow-focus aria-invalid:border-2 aria-invalid:border-danger"
          />
          <datalist id={listId}>
            {societies.map((s) => (
              <option key={s.slug} value={`${s.name}, ${s.area}`} />
            ))}
          </datalist>
        </div>
        <button
          type="submit"
          className="inline-flex min-h-14 items-center justify-center rounded-sm border border-primary bg-primary px-4 text-base leading-5 font-semibold tracking-[0.01em] text-on-primary outline-none focus-visible:shadow-focus"
        >
          Find my society
        </button>
      </div>
      <p id={`${inputId}-msg`} role="alert" className="min-h-0 text-sm leading-5 text-danger">
        {message}
      </p>
    </form>
  );
}
