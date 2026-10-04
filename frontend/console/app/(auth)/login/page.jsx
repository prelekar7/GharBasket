import { connection } from "next/server";
import { Suspense } from "react";
import { devLoginEnabled } from "@/lib/session.js";
import { LoginForm } from "./LoginForm.jsx";

export const metadata = { title: "Sign in" };

/** Decided per request, so one build can run with or without dev sign-in. */
async function DevSignIn() {
  await connection();
  return devLoginEnabled ? (
    <LoginForm />
  ) : (
    <p role="alert" className="font-semibold text-danger">
      Sign-in is not available on this server yet.
    </p>
  );
}

export default function LoginPage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 px-5 py-12">
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          GharBasket Console
        </h1>
        <p className="text-fg-secondary">
          Phone sign-in arrives with the identity module (M1). For now, pick a role to explore the
          console.
        </p>
      </div>
      <Suspense>
        <DevSignIn />
      </Suspense>
    </main>
  );
}
