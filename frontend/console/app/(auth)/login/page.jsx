import { connection } from "next/server";
import { Suspense } from "react";
import { Wordmark } from "@/components/Wordmark.jsx";
import { devLoginEnabled, isMockApi, MOCK_ROLES, ROLES } from "@/lib/session.js";
import { phoneSignInConfig } from "@/lib/sign-in.js";
import { DevButtons, LoginForm } from "./LoginForm.jsx";
import { PhoneSignIn } from "./PhoneSignIn.jsx";

export const metadata = { title: "Sign in" };

/**
 * Decided per request, so one build can serve any setup: the real phone sign-in when the console
 * talks to the real API and has a Firebase config (lib/sign-in.js), the dev stand-in while dev
 * sign-in is on, and a plain refusal otherwise.
 */
async function SignIn() {
  await connection();
  const firebase = phoneSignInConfig(process.env);
  const roles = isMockApi() ? MOCK_ROLES : Object.entries(ROLES);
  if (firebase)
    return (
      <div className="flex flex-col gap-5">
        <PhoneSignIn firebase={firebase} />
        {devLoginEnabled && (
          <DevButtons roles={roles} note="Development only: skip the SMS (a dev API only)." />
        )}
      </div>
    );
  return devLoginEnabled ? (
    <LoginForm roles={roles} />
  ) : (
    <p role="alert" className="font-semibold text-danger">
      Sign-in is not available on this server yet.
    </p>
  );
}

export default function LoginPage() {
  return (
    <div className="grid min-h-dvh place-items-center p-6">
      <main className="flex w-full max-w-[420px] flex-col gap-5 rounded-lg bg-surface p-8 shadow-card">
        <div className="flex flex-col gap-1.5">
          <Wordmark size="lg" />
          <h1 className="font-display text-[32px] leading-[38px] font-semibold">Sign in</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            For GharBasket staff: admins, hub managers and franchise owners. Your role decides what
            you can see and change.
          </p>
        </div>
        <Suspense>
          <SignIn />
        </Suspense>
        <p className="text-sm leading-5 text-fg-secondary">
          Same phone sign-in as the app. Signed in on a shared computer? Sign out when you leave:
          the session ends at once.
        </p>
      </main>
    </div>
  );
}
