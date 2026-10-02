import { cookies } from "next/headers";
import { Suspense } from "react";
import { signOut } from "@/app/(auth)/login/actions.js";
import { Sidebar } from "@/components/Sidebar.jsx";
import { NAV } from "@/lib/nav.js";
import { parseSession, ROLES, SESSION_COOKIE } from "@/lib/session.js";

async function currentRole() {
  return parseSession((await cookies()).get(SESSION_COOKIE)?.value);
}

/** The sections this role sees (proxy.js has already kept signed-out people out). */
async function RoleSidebar() {
  const role = await currentRole();
  const sections = role ? ROLES[role].sections : [];
  return <Sidebar groups={NAV.filter((g) => sections.includes(g.section))} />;
}

async function Account() {
  const role = await currentRole();
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="text-fg-secondary">{role ? ROLES[role].label : ""}</span>
      <form action={signOut}>
        <button
          type="submit"
          className="min-h-10 rounded-md px-3 font-semibold text-primary outline-none hover:bg-primary-soft focus-visible:shadow-focus"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}

/**
 * Console shell shared by the Admin, Hub, Franchise and Analytics groups: sidebar and top bar.
 * Hub switcher and ⌘K palette join with the operations screens.
 * @param {{ children: import("react").ReactNode }} props
 */
export default function ConsoleLayout({ children }) {
  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 h-dvh w-60 shrink-0 overflow-y-auto border-r border-line bg-surface">
        <div className="flex h-14 items-center px-6 font-display text-lg font-semibold">
          GharBasket
        </div>
        <Suspense>
          <RoleSidebar />
        </Suspense>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-end border-b border-line bg-surface px-6">
          <Suspense>
            <Account />
          </Suspense>
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
