import { formatWhen } from "@/lib/format.js";
import { actionLabel, changeLines } from "@/lib/recipes.js";

/**
 * Who changed the recipe and what, newest first (`GET /v1/admin/recipes/{id}/history`, from the audit log).
 * @param {{ items: import("@/lib/recipes.js").HistoryEntry[] }} props
 */
export function RecipeHistory({ items }) {
  return (
    <section className="flex flex-col gap-3.5 rounded-md border border-line bg-bg p-5">
      <h2 className="text-[17px] leading-6 font-bold">History</h2>
      {items.length === 0 ? (
        <p className="text-sm leading-5 text-fg-secondary">Nobody has changed this recipe yet.</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {items.map((e) => (
            <li key={`${e.at}-${e.action}`} className="flex flex-col gap-0.5 text-sm leading-5">
              <span className="font-semibold">
                {actionLabel(e.action)} by {e.by?.name ?? "someone"}
              </span>
              <span className="text-fg-secondary">{formatWhen(e.at)}</span>
              {changeLines(e).map((line) => (
                <span key={line} className="break-words text-fg-secondary">
                  {line}
                </span>
              ))}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
