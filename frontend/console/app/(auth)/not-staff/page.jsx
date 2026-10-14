import { LinkButton } from "@/components/LinkButton.jsx";
import { Wordmark } from "@/components/Wordmark.jsx";

export const metadata = { title: "Staff only" };

/**
 * Where someone lands who signed in with a number that holds no staff role (admin, hub manager,
 * franchise owner or NGO coordinator). The session is already ended. Public, like /login.
 */
export default function NotStaffPage() {
  return (
    <div className="grid min-h-dvh place-items-center p-6">
      <main className="flex w-full max-w-[420px] flex-col gap-5 rounded-lg bg-surface p-8 shadow-card">
        <div className="flex flex-col gap-1.5">
          <Wordmark size="lg" />
          <h1 className="font-display text-[32px] leading-[38px] font-semibold">
            This console is for GharBasket staff
          </h1>
          <p className="text-sm leading-5 text-fg-secondary">
            That number is signed up for the app but has no staff role, so you have been signed out
            of the console. Ask an admin to give you a role, or sign in with a staff number.
          </p>
        </div>
        <LinkButton href="/login" variant="primary">
          Sign in with another number
        </LinkButton>
      </main>
    </div>
  );
}
