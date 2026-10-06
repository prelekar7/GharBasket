import { NuqsAdapter } from "nuqs/adapters/next/app";
import { Suspense } from "react";
import { signOut } from "@/app/(auth)/login/actions.js";
import { CommandPalette } from "@/components/CommandPalette.jsx";
import { ConsoleToaster } from "@/components/catalog/ConsoleToaster.jsx";
import { HubSwitcher } from "@/components/HubSwitcher.jsx";
import { Sidebar } from "@/components/Sidebar.jsx";
import { Wordmark } from "@/components/Wordmark.jsx";
import { getPerson, getRole } from "@/lib/auth.js";
import { currentHub, listHubs } from "@/lib/hub.js";
import { isReady, navFor, paletteEntries } from "@/lib/nav.js";
import { initialsOf } from "@/lib/session.js";
import Loading from "./loading.jsx";

/** The role's sections (proxy.js has already kept signed-out people out). */
async function RoleSidebar() {
  const role = await getRole();
  return <Sidebar groups={role ? navFor(role) : []} />;
}

async function HeaderControls() {
  const [role, hubs, hub] = await Promise.all([getRole(), listHubs(), currentHub()]);
  return (
    <>
      <HubSwitcher hubs={hubs} value={hub.id} />
      {role && (
        <CommandPalette {...paletteEntries(role)} productsReady={isReady("/catalog/x") === true} />
      )}
    </>
  );
}

async function Account() {
  const person = await getPerson();
  return (
    <div className="flex items-center gap-3">
      {person && (
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="grid size-9 place-items-center rounded-full bg-accent-soft font-bold text-accent-fg"
          >
            {initialsOf(person.name)}
          </span>
          <span className="flex flex-col leading-[18px]">
            <strong className="text-sm">{person.name}</strong>
            <span className="text-xs text-fg-secondary">{person.roleLabel}</span>
          </span>
        </div>
      )}
      <form action={signOut}>
        <button
          type="submit"
          className="min-h-11 rounded-sm px-3 text-sm font-semibold text-primary outline-none hover:bg-primary-soft focus-visible:shadow-focus"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}

/**
 * Console shell shared by every console page: a sidebar (a top menu below 900px), and a top bar
 * with the hub switcher, the ⌘K palette, the signed-in person and Sign out. Everything that reads the session sits
 * in its own Suspense boundary, as cacheComponents needs.
 * @param {{ children: import("react").ReactNode }} props
 */
export default function ConsoleLayout({ children }) {
  return (
    <div className="min-h-dvh min-[900px]:flex">
      {/* The aside stretches to the page's full height so its background never stops at the viewport; the menu inside stays stuck to the top. */}
      <aside className="border-b border-line bg-surface min-[900px]:w-60 min-[900px]:shrink-0 min-[900px]:border-r min-[900px]:border-b-0">
        <div className="min-[900px]:sticky min-[900px]:top-0 min-[900px]:h-dvh min-[900px]:overflow-y-auto">
          <Suspense
            fallback={
              <div className="px-4 py-3 min-[900px]:px-6 min-[900px]:pt-5">
                <Wordmark />
              </div>
            }
          >
            <RoleSidebar />
          </Suspense>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-end justify-between gap-3 border-b border-line px-4 py-3 min-[900px]:px-6">
          <div className="flex flex-wrap items-end gap-3">
            <Suspense fallback={<div className="h-[76px] w-[220px]" />}>
              <HeaderControls />
            </Suspense>
          </div>
          <Suspense>
            <Account />
          </Suspense>
        </header>
        <main className="flex min-w-0 flex-1 flex-col gap-4 p-4 min-[900px]:p-6">
          <Suspense fallback={<Loading />}>
            {/* nuqs keeps page filters (search, date, status) in the URL; hub is a cookie, see lib/hub.js */}
            <NuqsAdapter>{children}</NuqsAdapter>
          </Suspense>
        </main>
      </div>
      <ConsoleToaster />
    </div>
  );
}
