"use client";

import {
  Badge,
  Button,
  EmptyState,
  formatPaise,
  MoneyText,
  Tabs,
  TextField,
  toast,
} from "@gharbasket/ui";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useState, useTransition } from "react";
import { KV, PageHead, Panel, TABLE, TD, TH } from "@/components/admin/Page.jsx";
import { whenLabel } from "@/lib/admin/format.js";
import { dayAndTime } from "@/lib/dates.js";
import { formatDay } from "@/lib/format.js";
import { approveDish, reopenDish, sendDishBack } from "./actions.js";

/** @typedef {import("@/lib/admin/types.js").TiffinMenuReview} TiffinMenuReview */

const TABS = /** @type {const} */ (["review", "live", "back"]);
/** The tab of each M2 menu status (§3.4): waiting is `submitted`, approved `approved`, sent back `sent_back`. */
const STATUS = /** @type {const} */ ({ review: "submitted", live: "approved", back: "sent_back" });
const STATE = /** @type {const} */ ({
  review: ["accent", "Waiting"],
  live: ["primary", "Approved"],
  back: ["outline", "Sent back"],
});
const EMPTY = {
  review: ["Nothing waiting", "When a kitchen submits a menu, it waits here for your approval."],
  live: [
    "No approved menus for the days ahead",
    "Menus you approve are listed here, and families can book them.",
  ],
  back: ["Nothing sent back", "Menus you send back to a kitchen, with your note, are listed here."],
};
/** What a menu's `rules` say it suits, in plain words. */
const RULE_LABELS = /** @type {const} */ ([
  ["jain_ok", "Jain"],
  ["no_onion_garlic", "No onion or garlic"],
  ["fasting_ok", "Fasting"],
  ["diabetic_ok", "Diabetic-friendly"],
]);

/** @param {TiffinMenuReview["rules"]} rules */
const suits = (rules) => RULE_LABELS.filter(([key]) => rules[key]).map(([, label]) => label);
/** @param {TiffinMenuReview} m */
const kitchenName = (m) => m.kitchen.display_name ?? "A kitchen no longer on the list";
/** @param {TiffinMenuReview} m */
const dishLine = (m) => m.dishes.map((d) => d.name).join(", ");

/**
 * Tiffin menu approvals: the hub manager approves each menu a kitchen submits before families can
 * book it. The price families pay is the menu's own and the kitchen's pay comes from GharBasket's
 * rate card; this page shows them and never changes them.
 * @param {{ menus: TiffinMenuReview[], hubName: string, kitchenPayPaise: number | null, now: number }} props
 */
export function TiffinView({ menus, hubName, kitchenPayPaise, now }) {
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("review").withOptions({ history: "replace" }),
  );
  const [pickedId, setPickedId] = useState(/** @type {string | null} */ (null));
  const list = menus.filter((m) => m.status === STATUS[tab]);
  const picked = list.find((m) => m.id === pickedId) ?? list[0] ?? null;
  const count = (/** @type {typeof TABS[number]} */ t) =>
    menus.filter((m) => m.status === STATUS[t]).length;

  return (
    <>
      <PageHead title="Tiffin menus">
        Kitchens submit their menus for the days ahead; the hub manager approves each one before
        families can book it. GharBasket sets the price, never the kitchen or the AI.
      </PageHead>
      <Tabs
        label="Menu lists"
        tabs={[
          { value: "review", label: "Waiting for approval", count: count("review") },
          { value: "live", label: "Approved", count: count("live") },
          { value: "back", label: "Sent back", count: count("back") },
        ]}
        value={tab}
        onChange={(v) => void setTab(/** @type {typeof tab} */ (v))}
      >
        {list.length === 0 ? (
          <EmptyState title={EMPTY[tab][0]} body={EMPTY[tab][1]} />
        ) : (
          <div className="grid items-start gap-4 min-[1000px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
            <div className="overflow-x-auto rounded-md border border-line bg-bg">
              <table className={TABLE}>
                <caption className="sr-only">
                  {tab === "review"
                    ? "Menus waiting for approval"
                    : `Menus ${STATE[tab][1].toLowerCase()} at ${hubName}`}
                </caption>
                <thead>
                  <tr>
                    <th className={TH}>Menu</th>
                    <th className={TH}>Kitchen</th>
                    <th className={TH}>Day</th>
                    <th className={TH}>Suits</th>
                    <th className={`${TH} text-right`}>Price</th>
                    <th className={TH}>State</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((m) => (
                    <tr
                      key={m.id}
                      aria-selected={picked?.id === m.id}
                      className={picked?.id === m.id ? "bg-primary-soft" : undefined}
                    >
                      <td className={TD}>
                        <button
                          type="button"
                          aria-pressed={picked?.id === m.id}
                          onClick={() => setPickedId(m.id)}
                          className="flex min-h-11 cursor-default flex-col rounded-xs text-left outline-none focus-visible:shadow-focus"
                        >
                          <span className="font-semibold">{m.product.name}</span>
                          <span className="text-[13px] leading-[18px] text-fg-secondary">
                            {dishLine(m)}
                          </span>
                        </button>
                      </td>
                      <td className={TD}>{kitchenName(m)}</td>
                      <td className={TD}>
                        {formatDay(m.date)}, {m.meal}
                      </td>
                      <td className={TD}>{suits(m.rules).join(" · ") || "No special diet"}</td>
                      <td className={`${TD} text-right`}>
                        <MoneyText paise={m.price_paise} size="sm" />
                      </td>
                      <td className={TD}>
                        <Badge tone={STATE[tab][0]}>{STATE[tab][1]}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {picked && (
              <ReviewPanel
                key={picked.id}
                menu={picked}
                kitchenPayPaise={kitchenPayPaise}
                now={now}
              />
            )}
          </div>
        )}
      </Tabs>
    </>
  );
}

/**
 * The menu being reviewed: its facts, and Approve or Send back while it waits, or Move back to
 * waiting once decided and until its cut-off.
 * @param {{ menu: TiffinMenuReview, kitchenPayPaise: number | null, now: number }} props
 */
function ReviewPanel({ menu, kitchenPayPaise, now }) {
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, run] = useTransition();
  const kitchen = kitchenName(menu);
  const first = kitchen.split(" ")[0];
  const waiting = menu.status === "submitted";
  const open = Date.parse(menu.order_by) > now;

  /** The Undo of a decision: reopen, and say so when the cut-off or someone else got there first. @param {string} message */
  const undoable = (message) =>
    toast(message, {
      action: {
        label: "Undo",
        onClick: async () => {
          const result = await reopenDish(menu.id);
          if (!result.ok) toast(result.message);
        },
      },
    });

  function approve() {
    setProblem("");
    run(async () => {
      const result = await approveDish(menu.id);
      if (!result.ok) return setProblem(result.message);
      undoable(`${menu.product.name} approved`);
    });
  }

  function sendBack() {
    if (!note.trim())
      return setProblem(`Tell ${first} what to change, so the note shows in the app.`);
    setProblem("");
    run(async () => {
      const result = await sendDishBack(menu.id, note.trim());
      if (!result.ok) return setProblem(result.message);
      undoable(`${menu.product.name} sent back to ${first}`);
    });
  }

  function reopen() {
    setProblem("");
    run(async () => {
      const result = await reopenDish(menu.id);
      if (!result.ok) return setProblem(result.message);
      toast(`${menu.product.name} is waiting for approval again`);
    });
  }

  return (
    <Panel label="Review menu">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">{menu.product.name}</h2>
        {menu.status === "approved" && <Badge tone="primary">Approved</Badge>}
        {menu.status === "sent_back" && <Badge tone="outline">Sent back</Badge>}
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        {dishLine(menu)}. Submitted by {kitchen} for {menu.meal} on {formatDay(menu.date)}. Recipe
        lines come from the catalog, so allergens and fasting rules are checked automatically.
      </p>
      <div>
        <KV k="Price to families">{formatPaise(menu.price_paise)} (the menu's price)</KV>
        {kitchenPayPaise !== null && (
          <KV k="Kitchen is paid">{formatPaise(kitchenPayPaise)} a tiffin (the hub’s rate card)</KV>
        )}
        <KV k="Contains">{menu.contains.join(", ") || "Nothing listed"}</KV>
        <KV k="Fasting days">
          {menu.rules.fasting_ok ? "Suitable (no grains)" : "Not suitable (has grains)"}
        </KV>
        <KV k="Order by">{dayAndTime(menu.order_by)}</KV>
      </div>

      {waiting ? (
        <>
          <TextField
            label="Note to the kitchen, if you send it back"
            placeholder={`Shown to ${first} in her app`}
            value={note}
            onChange={setNote}
            className="max-w-none"
          />
          {problem && (
            <p role="alert" className="text-sm leading-5 font-semibold text-danger">
              {problem}
            </p>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Button block isDisabled={busy} onPress={sendBack}>
              Send back
            </Button>
            <Button variant="primary" block isDisabled={busy} onPress={approve}>
              Approve menu
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm leading-5 text-fg-secondary">
            {menu.status === "approved" ? "Approved" : "Sent back"}
            {menu.decided_by?.name ? ` by ${menu.decided_by.name}` : ""}
            {menu.decided_at ? ` · ${whenLabel(menu.decided_at, now)}` : ""}
            {menu.note ? `. Note to ${first}: "${menu.note}"` : ""}
          </p>
          {problem && (
            <p role="alert" className="text-sm leading-5 font-semibold text-danger">
              {problem}
            </p>
          )}
          {open ? (
            <Button block isDisabled={busy} onPress={reopen}>
              Move back to waiting
            </Button>
          ) : (
            <p className="text-sm leading-5 text-fg-secondary">
              The order-by time has passed, so this menu can no longer be changed.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
