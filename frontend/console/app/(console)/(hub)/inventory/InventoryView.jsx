"use client";

import { Badge, Button, EmptyState, Sheet, TextField, toast } from "@gharbasket/ui";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { formatDay, formatWhen } from "@/lib/format.js";
import { parseQty, withUnit } from "@/lib/ops-units.js";
import { countLot, loadLot } from "./actions.js";

/**
 * GET /v1/hubs/{id}/inventory and GET /v1/lots/{id} (M6 §3.4, built). Quantities are in the lot's unit.
 * @typedef {{ id: string, name: string, unit: string, source: "receipt" | "returned", grade: "A" | "B" | "C",
 *   qty_in: number, qty_on_hand: number, received_at: string, best_before: string }} Lot
 * @typedef {{ product_id: string, name: string, unit: string, on_hand_qty: number,
 *   packed_not_drawn_qty: number, lots: Lot[] }} Product
 * @typedef {{ kind: string, qty: number, reason: string | null, by: { name: string | null } | null,
 *   at: string }} Move
 */

const HOUR = 3_600_000;
const SOON_H = 48;
const KIND = {
  receive: "Received",
  return: "Returned bag",
  pack: "Packed",
  waste: "Waste",
  offer: "Offered",
  count: "Count",
};

/** How close a lot is to its best-before, measured from the API's own clock. @param {string} bestBefore @param {string} asOf */
function expiry(bestBefore, asOf) {
  const hours = (Date.parse(bestBefore) - Date.parse(asOf)) / HOUR;
  if (hours < 0) return /** @type {const} */ ({ tone: "danger", label: "Past best-before" });
  if (hours < SOON_H) return /** @type {const} */ ({ tone: "accent", label: "Expires soon" });
  return null;
}

/** @param {{ asOf: string, items: Product[] }} props */
export function InventoryView({ asOf, items }) {
  const router = useRouter();
  const [open, setOpen] = useState(/** @type {string | null} */ (null));
  const [lot, setLot] = useState(/** @type {(Lot & { moves: Move[] }) | null} */ (null));
  const [form, setForm] = useState({ qty: "", reason: "" });
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  /** @param {string} id */
  function show(id) {
    setOpen(id);
    setLot(null);
    setForm({ qty: "", reason: "" });
    setErrors({});
    setKey(crypto.randomUUID());
    startTransition(async () => {
      const res = await loadLot(id);
      if (res.ok) setLot(res.data);
      else {
        toast(res.message);
        setOpen(null);
      }
    });
  }

  function save() {
    if (!lot) return;
    const qty = parseQty(form.qty, lot.unit, 0);
    /** @type {Record<string, string>} */
    const local = {};
    if (qty === null) local.qty_on_hand = "Enter what you counted: 0 or more, whole for counts";
    if (form.reason.trim().length < 3) local.reason = "Say why, in at least 3 characters";
    if (qty === null || Object.keys(local).length) return setErrors(local);
    startTransition(async () => {
      const res = await countLot(lot.id, qty, form.reason.trim(), key);
      if (!res.ok) {
        setErrors(
          res.fields.qty_on_hand || res.fields.reason ? res.fields : { qty_on_hand: res.message },
        );
        return;
      }
      toast(`${lot.name} counted: ${withUnit(res.data.qty_on_hand, lot.unit)} on hand.`);
      setOpen(null);
      router.refresh();
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">Inventory</h1>
        <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
          What the hub holds, product by product, lot by lot with the first to expire on top. Count
          a lot on the shelf to correct its stock. As of {formatWhen(asOf)}.
        </p>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="The shelves are empty"
          body="Lots appear here once goods are received."
        />
      ) : (
        items.map((p) => (
          <section
            key={p.product_id}
            className="flex min-w-0 flex-col gap-1 rounded-md bg-surface p-4"
          >
            <h2 className="m-0 flex flex-wrap items-baseline gap-x-3 text-[17px] leading-6 font-bold">
              {p.name}
              <span className="text-sm font-normal text-fg-secondary">
                {withUnit(p.on_hand_qty, p.unit)} on hand
                {p.packed_not_drawn_qty > 0 &&
                  `, ${withUnit(p.packed_not_drawn_qty, p.unit)} packed and not yet drawn down`}
              </span>
            </h2>
            {p.lots.map((l) => {
              const flag = expiry(l.best_before, asOf);
              return (
                <div
                  key={l.id}
                  className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-2 last:border-b-0"
                >
                  <span className="flex flex-col">
                    <strong>
                      {withUnit(l.qty_on_hand, l.unit)}
                      {l.qty_on_hand !== l.qty_in && (
                        <span className="font-normal text-fg-secondary"> of {l.qty_in}</span>
                      )}
                    </strong>
                    <span className="text-sm text-fg-secondary">
                      Best before {formatWhen(l.best_before)} · received {formatDay(l.received_at)}
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    {flag && <Badge tone={flag.tone}>{flag.label}</Badge>}
                    {l.source === "returned" && <Badge tone="outline">Returned bag</Badge>}
                    <Badge tone={l.grade === "C" ? "neutral" : "primary"}>Grade {l.grade}</Badge>
                    <Button isDisabled={busy && open === null} onPress={() => show(l.id)}>
                      Details and count
                    </Button>
                  </span>
                </div>
              );
            })}
          </section>
        ))
      )}

      <Sheet
        title={lot ? `${lot.name}: record a count` : "Loading lot"}
        description={
          lot
            ? `${withUnit(lot.qty_on_hand, lot.unit)} on hand of ${lot.qty_in} received. Best before ${formatWhen(lot.best_before)}.`
            : undefined
        }
        open={open !== null}
        onOpenChange={(o) => !o && setOpen(null)}
        footer={
          <>
            <Button onPress={() => setOpen(null)}>Close</Button>
            <Button variant="primary" isDisabled={busy || !lot} onPress={save}>
              Save count
            </Button>
          </>
        }
      >
        {lot && (
          <>
            <TextField
              label={`Counted on the shelf (${lot.unit})`}
              description="What is really there now; the lot’s stock is set to this"
              inputMode="decimal"
              value={form.qty}
              onChange={(v) => {
                setForm((f) => ({ ...f, qty: v }));
                setKey(crypto.randomUUID()); // another count is another request
                setErrors((e) => ({ ...e, qty_on_hand: "" }));
              }}
              isInvalid={!!errors.qty_on_hand}
              errorMessage={errors.qty_on_hand}
            />
            <TextField
              label="Reason"
              description="3 to 200 characters, kept in the audit log"
              value={form.reason}
              onChange={(v) => {
                setForm((f) => ({ ...f, reason: v }));
                setKey(crypto.randomUUID());
                setErrors((e) => ({ ...e, reason: "" }));
              }}
              isInvalid={!!errors.reason}
              errorMessage={errors.reason}
            />
            <h3 className="m-0 mt-2 text-sm font-bold">Stock moves, oldest first</h3>
            {lot.moves.map((m) => (
              <div
                key={`${m.at}-${m.kind}-${m.qty}`}
                className="flex justify-between gap-3 border-b border-line py-1.5 text-sm"
              >
                <span className="flex flex-col">
                  {KIND[/** @type {keyof typeof KIND} */ (m.kind)] ?? m.kind}
                  <span className="text-fg-secondary">
                    {formatWhen(m.at)} · {m.by?.name ?? "Automatic"}
                    {m.reason && ` · ${m.reason}`}
                  </span>
                </span>
                <strong>
                  {m.qty > 0 ? "+" : ""}
                  {m.qty} {lot.unit}
                </strong>
              </div>
            ))}
          </>
        )}
      </Sheet>
    </div>
  );
}
