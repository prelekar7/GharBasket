import { LinkButton } from "@/components/LinkButton.jsx";
import { Wordmark } from "@/components/Wordmark.jsx";

export const metadata = { title: "Staff only" };

/**
 * Where someone lands who proved a number that holds no staff role (admin, hub manager, franchise
 * owner, NGO coordinator or catalog editor): the API refused it (403 `auth.not_staff`, no account
 * made) or a session without a staff role was ended. Public, like /login.
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
            That number has no staff role, so it cannot open the console. If you use the GharBasket
            app, your account there is not changed. Ask an admin to give you a role, or sign in with
            a staff number.
          </p>
        </div>
        <LinkButton href="/login" variant="primary">
          Sign in with another number
        </LinkButton>
      </main>
    </div>
  );
}
