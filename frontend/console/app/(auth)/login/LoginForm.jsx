"use client";

import { Button, TextField } from "@gharbasket/ui";
import { useSearchParams } from "next/navigation";
import { useActionState, useEffect, useState, useTransition } from "react";
import { useFormStatus } from "react-dom";
import { requestCode, signIn, signInWithPhone } from "./actions.js";

/** Seconds before "Resend" is offered, as in Login.dc.html. */
const RESEND_SECONDS = 24;

/** "9800001011" becomes "98000 01011". @param {string} digits */
const spaced = (digits) => `${digits.slice(0, 5)} ${digits.slice(5)}`;

/** 24 becomes "0:24". @param {number} s */
const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** @param {{ label: string, variant: "primary" | "secondary" | "quiet" }} props */
function Submit({ label, variant }) {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" variant={variant} size="lg" block isPending={pending}>
      {label}
    </Button>
  );
}

/**
 * The staff sign-in of Login.dc.html in two steps: the mobile number, then the 6-digit code ("Sent by
 * SMS to +91 98000 01011. Resend in 0:24.") with a live countdown, a Resend button once it ends and a
 * way back to change the number. Real phone OTP arrives with the identity module (M1, see
 * `requestCode` in actions.js); until then the dev form accepts the seed staff phones with the code
 * 123456, and the buttons below skip both steps for each role (only while dev sign-in is on).
 *
 * `roles` are the dev buttons: the mock sign-in's three, or every staff role against the real API.
 * @param {{ roles: [string, { label: string, person: string }][] }} props
 */
export function LoginForm({ roles }) {
  const next = useSearchParams().get("next") ?? "";
  const [sent, send] = useActionState(requestCode, {});
  const [verified, verify] = useActionState(signInWithPhone, {});
  const [resending, startResend] = useTransition();
  // "Change number" hides the code step for the send it was pressed on; a new send brings it back.
  const [dismissed, setDismissed] = useState(/** @type {number | undefined} */ (undefined));
  const [secondsLeft, setSecondsLeft] = useState(RESEND_SECONDS);
  const phone = sent.phone;
  const onCodeStep = phone !== undefined && sent.nonce !== dismissed;

  // Every send (the first or a resend) restarts the countdown.
  useEffect(() => {
    if (!sent.nonce) return;
    setSecondsLeft(RESEND_SECONDS);
    const timer = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(timer);
  }, [sent.nonce]);

  return (
    <div className="flex flex-col gap-5">
      {onCodeStep ? (
        <form action={verify} className="flex flex-col gap-5">
          <input type="hidden" name="next" value={next} />
          <input type="hidden" name="phone" value={phone} />
          <TextField
            label="Mobile number"
            type="tel"
            prefix="+91"
            value={spaced(phone)}
            isReadOnly
          />
          <TextField
            label="6-digit code"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            placeholder="••••••"
            description={
              secondsLeft > 0
                ? `Sent by SMS to +91 ${spaced(phone)}. Resend in ${clock(secondsLeft)}.`
                : `Sent by SMS to +91 ${spaced(phone)}.`
            }
            isInvalid={!!verified.error}
            errorMessage={verified.error}
          />
          <Submit label="Verify and sign in" variant="primary" />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button
              variant="quiet"
              isDisabled={secondsLeft > 0 || resending}
              onPress={() =>
                startResend(() => {
                  const form = new FormData();
                  form.set("phone", phone);
                  send(form);
                })
              }
            >
              Resend code
            </Button>
            <Button variant="quiet" onPress={() => setDismissed(sent.nonce)}>
              Change number
            </Button>
          </div>
        </form>
      ) : (
        <form action={send} className="flex flex-col gap-5">
          <TextField
            label="Mobile number"
            name="phone"
            type="tel"
            inputMode="tel"
            prefix="+91"
            autoComplete="tel-national"
            defaultValue={phone ? spaced(phone) : "98000 01011"}
            isInvalid={!!sent.error}
            errorMessage={sent.error}
          />
          <Submit label="Send code" variant="primary" />
        </form>
      )}
      <div className="flex flex-col gap-2 border-t border-line pt-4">
        <p className="text-sm leading-5 text-fg-secondary">
          Development only: skip the code. While phone sign-in is not live, the code is 123456.
        </p>
        {roles.map(([role, { label, person }]) => (
          <form key={role} action={signIn}>
            <input type="hidden" name="role" value={role} />
            <input type="hidden" name="next" value={next} />
            <Submit label={`Continue as ${person}, ${label.toLowerCase()}`} variant="secondary" />
          </form>
        ))}
      </div>
    </div>
  );
}
