"use client";

import { Select } from "@gharbasket/ui";
import { useOptimistic, useTransition } from "react";
import { setHub } from "@/app/(console)/actions.js";

/**
 * The top-bar hub switcher. The choice lives in the `gb_hub` cookie (see lib/hub.js); changing it
 * runs the `setHub` Server Action, which refreshes the page for the new hub.
 * @param {{ hubs: import("../lib/hub.js").Hub[], value: string }} props
 */
export function HubSwitcher({ hubs, value }) {
  const [, startTransition] = useTransition();
  const [shown, setShown] = useOptimistic(value);
  return (
    <div className="w-[220px] max-w-full">
      <Select
        label="Hub"
        options={hubs.map((h) => ({ value: h.id, label: h.name }))}
        value={shown}
        onChange={(next) =>
          startTransition(async () => {
            setShown(next);
            await setHub(next);
          })
        }
      />
    </div>
  );
}
