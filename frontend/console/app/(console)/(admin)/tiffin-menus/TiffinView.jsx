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
import { useRef, useState, useTransition } from "react";
import { KV, PageHead, Panel, TABLE, TD, TH } from "@/components/admin/Page.jsx";
import { whenLabel } from "@/lib/admin/format.js";
import { approveDish, reopenDish, sendDishBack } from "./actions.js";

/** @typedef {import("@/lib/admin/types.js").TiffinDish} TiffinDish */

const TABS = /** @type {const} */ (["review", "live", "back"]);
const STATE = /** @type {const} */ ({
  review: ["accent", "Waiting"],
  live: ["primary", "Approved"],
  back: ["outline", "Sent back"],
});
const EMPTY = {
  review: ["Nothing waiting", "When a kitchen proposes a dish, it waits here for your approval."],
  live: [
    "No approved dishes yet",
    "Dishes you approve are listed here, and families can book them.",
  ],
  back: [
    "Nothing sent back",
    "Dishes you send back to a kitchen, with your note, are listed here.",
  ],
};

/**
 * Tiffin menu approvals: the hub manager approves each dish a kitchen proposes before families can
 * book it. The price families pay and the kitchen's pay come from the catalog and GharBasket's rate
 * card; this page shows them and never changes them.
 * @param {{ dishes: TiffinDish[], hubName: string, now: number }} props
 */
export function TiffinView({ dishes, hubName, now }) {
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("review").withOptions({ history: "replace" }),
  );
  const [pickedId, setPickedId] = useState(/** @type {string | null} */ (null));
  const list = dishes.filter((d) => d.status === tab);
  const dish = list.find((d) => d.id === pickedId) ?? list[0] ?? null;
  const count = (/** @type {TiffinDish["status"]} */ s) =>
    dishes.filter((d) => d.status === s).length;

  return (
    <>
      <PageHead title="Tiffin menus">
        Kitchens propose their dishes; the hub manager approves each one before families can book
        it. GharBasket sets the price, never the kitchen or the AI.
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
                    ? "Dishes waiting for approval"
                    : `Dishes ${STATE[tab][1].toLowerCase()} at ${hubName}`}
                </caption>
                <thead>
                  <tr>
                    <th className={TH}>Dish</th>
                    <th className={TH}>Kitchen</th>
                    <th className={TH}>Suits</th>
                    <th className={`${TH} text-right`}>Price</th>
                    <th className={TH}>State</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((d) => (
                    <tr
                      key={d.id}
                      aria-selected={dish?.id === d.id}
                      className={dish?.id === d.id ? "bg-primary-soft" : undefined}
                    >
                      <td className={TD}>
                        <button
                          type="button"
                          aria-pressed={dish?.id === d.id}
                          onClick={() => setPickedId(d.id)}
                          className="flex min-h-11 cursor-default flex-col rounded-xs text-left outline-none focus-visible:shadow-focus"
                        >
                          <span className="font-semibold">{d.name}</span>
                          <span className="text-[13px] leading-[18px] text-fg-secondary">
                            {d.components}
                          </span>
                        </button>
                      </td>
                      <td className={TD}>{d.kitchen.name}</td>
                      <td className={TD}>{d.tags.join(" · ")}</td>
                      <td className={`${TD} text-right`}>
                        <MoneyText paise={d.price_paise} size="sm" />
                      </td>
                      <td className={TD}>
                        <Badge tone={STATE[d.status][0]}>{STATE[d.status][1]}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {dish && <ReviewPanel key={dish.id} dish={dish} now={now} />}
          </div>
        )}
      </Tabs>
    </>
  );
}

/**
 * The dish being reviewed: its facts, and Approve or Send back while it waits.
 * @param {{ dish: TiffinDish, now: number }} props
 */
function ReviewPanel({ dish, now }) {
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, run] = useTransition();
  const keys = useRef({ approve: "", back: "" });
  const first = dish.kitchen.name.split(" ")[0];
  const waiting = dish.status === "review";

  /** @param {string} message @param {string} id */
  const undoable = (message, id) =>
    toast(message, { action: { label: "Undo", onClick: () => void reopenDish(id) } });

  function approve() {
    run(async () => {
      keys.current.approve ||= crypto.randomUUID();
      const result = await approveDish(keys.current.approve, dish.id);
      if (!result.ok) {
        setProblem(result.message);
        return;
      }
      keys.current.approve = "";
      undoable(`${dish.name} approved`, dish.id);
    });
  }

  function sendBack() {
    if (!note.trim())
      return setProblem(`Tell ${first} what to change, so the note shows in the app.`);
    setProblem("");
    run(async () => {
      keys.current.back ||= crypto.randomUUID();
      const result = await sendDishBack(keys.current.back, dish.id, note.trim());
      if (!result.ok) {
        setProblem(result.message);
        return;
      }
      keys.current.back = "";
      undoable(`${dish.name} sent back to ${first}`, dish.id);
    });
  }

  return (
    <Panel label="Review dish">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">{dish.name}</h2>
        {waiting && dish.is_new && <Badge tone="accent">New dish</Badge>}
        {dish.status === "live" && <Badge tone="primary">Approved</Badge>}
        {dish.status === "back" && <Badge tone="outline">Sent back</Badge>}
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        Proposed by {dish.kitchen.name} for {dish.meal}. Recipe lines come from the catalog, so
        allergens and fasting rules are checked automatically.
      </p>
      <div>
        <KV k="Price to families">{formatPaise(dish.price_paise)} (catalog price)</KV>
        <KV k="Kitchen is paid">{formatPaise(dish.kitchen_pay_paise)} a tiffin</KV>
        <KV k="Contains">{dish.contains.join(", ")}</KV>
        <KV k="Fasting days">{dish.fasting_note}</KV>
        <KV k="Capacity">
          Within {first}'s {dish.daily_capacity} a day
        </KV>
      </div>

      {waiting ? (
        <>
          <TextField
            label="Note to the kitchen, if you send it back"
            placeholder={`Shown to ${first} in her app`}
            value={note}
            onChange={(v) => {
              keys.current.back = ""; // a changed note is a new request, so it needs a new key
              setNote(v);
            }}
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
              Approve dish
            </Button>
          </div>
        </>
      ) : (
        <p className="text-sm leading-5 text-fg-secondary">
          {dish.status === "live" ? "Approved" : "Sent back"} by {dish.decided_by} ·{" "}
          {dish.decided_at ? whenLabel(dish.decided_at, now) : ""}
          {dish.note ? `. Note to ${first}: "${dish.note}"` : ""}
        </p>
      )}
    </Panel>
  );
}
