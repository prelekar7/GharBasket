"use client";

import { Button, TextField } from "@gharbasket/ui";
import { useSearchParams } from "next/navigation";
import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { ROLES } from "@/lib/session.js";
import { signIn, signInWithPhone } from "./actions.js";

/** @param {{ label: string, variant: "primary" | "secondary" }} props */
function Submit({ label, variant }) {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" variant={variant} size="lg" block isPending={pending}>
      {label}
    </Button>
  );
}

/**
 * The staff sign-in of Login.dc.html. Real phone OTP arrives with the identity module (M1, POST
 * /v1/auth/exchange); until then this dev form accepts the seed staff phones with the code 123456,
 * and the buttons below skip the form for each role (both only while dev sign-in is on).
 */
export function LoginForm() {
  const next = useSearchParams().get("next") ?? "";
  const [state, action] = useActionState(signInWithPhone, {});
  return (
    <div className="flex flex-col gap-5">
      <form action={action} className="flex flex-col gap-5">
        <input type="hidden" name="next" value={next} />
        <TextField
          label="Mobile number"
          name="phone"
          type="tel"
          inputMode="tel"
          prefix="+91"
          autoComplete="tel-national"
          defaultValue="98000 01011"
          isInvalid={state.field === "phone"}
          errorMessage={state.field === "phone" ? state.error : undefined}
        />
        <TextField
          label="6-digit code"
          name="code"
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="••••••"
          description="While phone sign-in is not live, the code is 123456."
          isInvalid={state.field === "code"}
          errorMessage={state.field === "code" ? state.error : undefined}
        />
        <Submit label="Verify and sign in" variant="primary" />
      </form>
      <div className="flex flex-col gap-2 border-t border-line pt-4">
        <p className="text-sm leading-5 text-fg-secondary">Development only: skip the code.</p>
        {Object.entries(ROLES).map(([role, { label, person }]) => (
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
