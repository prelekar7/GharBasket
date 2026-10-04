"use client";

import { Button } from "@gharbasket/ui";
import { useSearchParams } from "next/navigation";
import { useFormStatus } from "react-dom";
import { ROLES } from "@/lib/session.js";
import { signIn } from "./actions.js";

/** @param {{ label: string }} props */
function Submit({ label }) {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" variant="secondary" size="lg" block isPending={pending}>
      {label}
    </Button>
  );
}

export function LoginForm() {
  const next = useSearchParams().get("next") ?? "";
  return (
    <div className="flex flex-col gap-3">
      {Object.entries(ROLES).map(([role, { label }]) => (
        <form key={role} action={signIn}>
          <input type="hidden" name="role" value={role} />
          <input type="hidden" name="next" value={next} />
          <Submit label={`Continue as ${label}`} />
        </form>
      ))}
    </div>
  );
}
