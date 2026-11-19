"use client";

import { Button, TextField } from "@gharbasket/ui";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { phoneErrorMessage } from "@/lib/sign-in.js";
import { signInWithFirebase } from "./actions.js";

/** Seconds before "Resend" is offered: Firebase refuses a new SMS to the same number sooner. */
const RESEND_SECONDS = 24;

/** The element the invisible reCAPTCHA renders into (it must exist when a code is sent). */
const RECAPTCHA_ID = "staff-recaptcha";

/** "9800001011" becomes "98000 01011". @param {string} digits */
const spaced = (digits) => `${digits.slice(0, 5)} ${digits.slice(5)}`;

/** 24 becomes "0:24". @param {number} s */
const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/** The 10 digits of an Indian mobile number as typed, or null. @param {string} value */
function digitsOf(value) {
  const digits = value.replace(/\D/g, "");
  const phone = digits.length === 12 && digits.startsWith("91") ? digits.slice(2) : digits;
  return /^[6-9]\d{9}$/.test(phone) ? phone : null;
}

/** @type {Promise<{ auth: import("firebase/auth").Auth, sdk: typeof import("firebase/auth") }> | null} */
let firebase = null;

/**
 * The Firebase Auth instance, loaded only on this page. In-memory persistence: Firebase is used only
 * to prove the phone, and the console signs out of it as soon as it has the ID token, so no Firebase
 * session outlives ours (as in the app, frontend/mobile/src/lib/otp.js).
 * @param {import("@/lib/sign-in.js").FirebaseWebConfig} config
 */
function firebaseAuth(config) {
  firebase ??= Promise.all([import("firebase/app"), import("firebase/auth")]).then(
    ([{ initializeApp }, sdk]) => {
      const { testMode, ...options } = config;
      const auth = sdk.initializeAuth(initializeApp(options, "staff-console"), {
        persistence: sdk.inMemoryPersistence,
      });
      auth.settings.appVerificationDisabledForTesting = testMode;
      return { auth, sdk };
    },
  );
  return firebase;
}

/**
 * The real staff sign-in (Login.dc.html's two steps): the mobile number, then the 6-digit SMS code
 * from Firebase. The browser gets a Firebase ID token and the server action exchanges it with the
 * API (`staff_only`), which decides who is staff.
 * @param {{ firebase: import("@/lib/sign-in.js").FirebaseWebConfig }} props
 */
export function PhoneSignIn({ firebase: config }) {
  const next = useSearchParams().get("next") ?? "";
  const [phone, setPhone] = useState(/** @type {string | null} */ (null));
  const [error, setError] = useState(/** @type {string | null} */ (null));
  const [sending, setSending] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [verifying, startVerify] = useTransition();
  const confirmation = useRef(
    /** @type {import("firebase/auth").ConfirmationResult | null} */ (null),
  );
  const verifier = useRef(/** @type {import("firebase/auth").RecaptchaVerifier | null} */ (null));

  useEffect(() => {
    if (secondsLeft <= 0) return;
    const timer = setTimeout(() => setSecondsLeft((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [secondsLeft]);

  // The widget belongs to this page; leaving it removes it.
  useEffect(() => () => verifier.current?.clear(), []);

  /** @param {string} digits */
  async function send(digits) {
    setError(null);
    setSending(true);
    try {
      const { auth, sdk } = await firebaseAuth(config);
      // "Resend code" builds a new widget in the same element, so the old one goes first.
      verifier.current?.clear();
      verifier.current = new sdk.RecaptchaVerifier(auth, RECAPTCHA_ID, { size: "invisible" });
      confirmation.current = await sdk.signInWithPhoneNumber(
        auth,
        `+91${digits}`,
        verifier.current,
      );
      setPhone(digits);
      setSecondsLeft(RESEND_SECONDS);
    } catch (e) {
      setError(phoneErrorMessage(e));
    } finally {
      setSending(false);
    }
  }

  /** @param {string} code */
  function verify(code) {
    setError(null);
    startVerify(async () => {
      try {
        const pending = confirmation.current;
        if (!pending) throw new Error("no code sent");
        const { user } = await pending.confirm(code);
        const idToken = await user.getIdToken();
        const { auth, sdk } = await firebaseAuth(config);
        await sdk.signOut(auth);
        // Success redirects; only a refusal comes back.
        const refused = await signInWithFirebase(idToken, next);
        if (refused?.error) setError(refused.error);
      } catch (e) {
        setError(phoneErrorMessage(e));
      }
    });
  }

  return (
    <div className="flex flex-col gap-5">
      {phone ? (
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            const code = String(new FormData(event.currentTarget).get("code") ?? "").trim();
            if (!/^\d{6}$/.test(code)) setError("Enter the 6-digit code from the SMS.");
            else verify(code);
          }}
        >
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
            isInvalid={!!error}
            errorMessage={error ?? undefined}
          />
          <Button type="submit" variant="primary" size="lg" block isPending={verifying}>
            Verify and sign in
          </Button>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button
              variant="quiet"
              isDisabled={secondsLeft > 0 || sending || verifying}
              isPending={sending}
              onPress={() => send(phone)}
            >
              Resend code
            </Button>
            <Button
              variant="quiet"
              isDisabled={verifying}
              onPress={() => {
                confirmation.current = null;
                setError(null);
                setPhone(null);
              }}
            >
              Change number
            </Button>
          </div>
        </form>
      ) : (
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            const digits = digitsOf(String(new FormData(event.currentTarget).get("phone") ?? ""));
            if (!digits) setError("Enter the 10-digit mobile number.");
            else send(digits);
          }}
        >
          <TextField
            label="Mobile number"
            name="phone"
            type="tel"
            inputMode="tel"
            prefix="+91"
            autoComplete="tel-national"
            isInvalid={!!error}
            errorMessage={error ?? undefined}
          />
          <Button type="submit" variant="primary" size="lg" block isPending={sending}>
            Send code
          </Button>
        </form>
      )}
      <div id={RECAPTCHA_ID} />
    </div>
  );
}
