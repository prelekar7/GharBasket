"use client";

import { Button, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useState, useTransition } from "react";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { formatPaiseExact, parseDecimal, toDecimalText } from "@/lib/money.js";
import { boxEconomics, sensitivities } from "@/lib/unit-economics.js";
import { saveAssumptions } from "./actions.js";

/** @typedef {import("@/lib/unit-economics.js").Assumptions} Assumptions */

/**
 * The seven inputs, in the order of the formula. `unit` says how the typed text becomes a whole number:
 * rupees → paise, percent → basis points (both are the text scaled by 100, never a float).
 * @type {{ key: keyof Assumptions, label: string, unit: "rupee" | "percent", help: string }[]}
 */
const FIELDS = [
  {
    key: "aov_paise",
    label: "Average box value (AOV)",
    unit: "rupee",
    help: "From September’s boxes",
  },
  { key: "margin_bp", label: "Margin on produce", unit: "percent", help: "After vendor cost" },
  {
    key: "delivery_paise",
    label: "Delivery per box",
    unit: "rupee",
    help: "Van and rider, shared across the route",
  },
  { key: "packing_paise", label: "Packing per box", unit: "rupee", help: "Packer time and bag" },
  {
    key: "captain_paise",
    label: "Captain commission per box",
    unit: "rupee",
    help: "Paid to the Society Captain",
  },
  { key: "payment_fee_bp", label: "Payment fee", unit: "percent", help: "Of the box value" },
  {
    key: "cac_paise",
    label: "Acquisition per box (CAC)",
    unit: "rupee",
    help: "Referral rewards and offers, spread per box",
  },
];

/** @param {string} text @param {"rupee" | "percent"} unit @returns {number | null} */
function parseField(text, unit) {
  const value = parseDecimal(text, 2);
  return value === null || (unit === "percent" && value > 10_000) ? null : value;
}

/** "₹40" or "24%" for a row label. @param {keyof Assumptions} key @param {number} value */
const short = (key, value) =>
  key.endsWith("_bp") ? `${toDecimalText(value)}%` : `₹${toDecimalText(value)}`;

/** @type {Record<string, string>} */
const MOVE_LABELS = { margin: "Margin", aov: "AOV", delivery: "Delivery", cac: "CAC" };
/** @type {Record<string, keyof Assumptions>} */
const MOVE_FIELDS = {
  margin: "margin_bp",
  aov: "aov_paise",
  delivery: "delivery_paise",
  cac: "cac_paise",
};

const dateTime = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

/**
 * The unit-economics calculator: change an assumption and the box's arithmetic updates as you type.
 * Saving writes the hub's assumptions, which the P&L then uses.
 * @param {{ data: import("./data.js").UnitEconomics }} props
 */
export function UnitEconomicsView({ data }) {
  const saved = data.assumptions;
  const [text, setText] = useState(() =>
    Object.fromEntries(FIELDS.map((f) => [f.key, toDecimalText(saved[f.key])])),
  );
  const [saving, startSaving] = useTransition();

  const parsed = FIELDS.map((f) => ({ ...f, value: parseField(text[f.key], f.unit) }));
  const valid = parsed.every((p) => p.value !== null);
  const draft = /** @type {Assumptions} */ (
    Object.fromEntries(parsed.map((p) => [p.key, p.value ?? saved[p.key]]))
  );
  const dirty = FIELDS.some((f) => draft[f.key] !== saved[f.key]);
  const box = boxEconomics(draft);

  /** @param {Assumptions} next @param {Assumptions | null} undoTo */
  function save(next, undoTo) {
    startSaving(async () => {
      const result = await saveAssumptions(next);
      if (!result.ok) {
        toast(result.message);
        return;
      }
      if (undoTo) {
        const back = undoTo;
        toast("Assumptions saved. The P&L uses them now.", {
          action: { label: "Undo", onClick: () => save(back, null) },
        });
      } else {
        toast("Assumptions put back.");
        setText(Object.fromEntries(FIELDS.map((f) => [f.key, toDecimalText(next[f.key])])));
      }
    });
  }

  const steps = [
    [
      `AOV × margin (${formatRupees(draft.aov_paise)} × ${short("margin_bp", draft.margin_bp)})`,
      box.margin_paise,
      false,
    ],
    ["− Delivery", box.delivery_paise, true],
    ["− Packing", box.packing_paise, true],
    ["− Captain commission", box.captain_paise, true],
    [`− Payment fee (${short("payment_fee_bp", draft.payment_fee_bp)})`, box.fee_paise, true],
    ["− Acquisition", box.cac_paise, true],
  ];

  const moves = sensitivities(draft)
    .sort((a, b) => Math.abs(b.change_paise) - Math.abs(a.change_paise))
    .map((m) => {
      const field = MOVE_FIELDS[m.key];
      const sign = m.change_paise > 0 ? "+" : m.change_paise < 0 ? "−" : "";
      return {
        id: m.key,
        what: {
          text: `${MOVE_LABELS[m.key]} ${short(field, m.from)} → ${short(field, m.to)}`,
          sub: m.key === "delivery" ? "A third society on the route" : undefined,
        },
        eff: { text: `${sign}${formatPaiseExact(Math.abs(m.change_paise))}`, strong: true },
      };
    });

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Unit economics · one box">
        Change an assumption and see what one box earns. Saved assumptions feed the P&amp;L; the
        history of changes is kept.
      </PageHeader>
      <p className="rounded-sm border border-line bg-bg px-3 py-2.5 font-mono text-[13px] leading-5">
        contribution per order = AOV × margin − (delivery + packing + captain commission + payment
        fee + CAC)
      </p>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-3">
        <section
          aria-label="Assumptions"
          className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
        >
          <h2 className="text-[17px] leading-6 font-bold">Assumptions</h2>
          {parsed.map((f) => (
            <TextField
              key={f.key}
              label={f.label}
              prefix={f.unit === "rupee" ? "₹" : "%"}
              description={f.help}
              inputMode="decimal"
              value={text[f.key]}
              onChange={(v) => setText((t) => ({ ...t, [f.key]: v }))}
              isInvalid={f.value === null}
              errorMessage={
                f.unit === "percent"
                  ? "Enter a percentage from 0 to 100, with up to two decimals."
                  : "Enter an amount in rupees, with up to two decimals."
              }
              className="!max-w-none"
            />
          ))}
          <Button
            variant="primary"
            block
            isDisabled={!valid || !dirty || saving}
            onPress={() => save(draft, saved)}
          >
            Save assumptions
          </Button>
        </section>

        <section
          aria-label="One box"
          className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
        >
          <h2 className="text-[17px] leading-6 font-bold">What one box earns</h2>
          {valid ? (
            <div role="status" aria-live="polite" className="flex flex-col">
              {steps.map(([label, paise]) => (
                <div
                  key={String(label)}
                  className="flex justify-between gap-3 border-b border-line py-2 tabular-nums"
                >
                  <span>{label}</span>
                  <span>{formatPaiseExact(/** @type {number} */ (paise))}</span>
                </div>
              ))}
              <div className="flex justify-between gap-3 border-t-2 border-fg pt-3 text-lg font-bold tabular-nums">
                <span>Contribution</span>
                <span>{formatPaiseExact(box.contribution_paise)}</span>
              </div>
              {box.contribution_paise < 0 && (
                <p className="mt-2 text-sm leading-5 font-semibold text-danger">
                  At these numbers every box loses money.
                </p>
              )}
            </div>
          ) : (
            <p role="status" className="text-sm leading-5 text-fg-secondary">
              Fix the marked numbers to see what a box earns.
            </p>
          )}
        </section>

        <section
          aria-label="What moves it most"
          className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
        >
          <h2 className="text-[17px] leading-6 font-bold">What moves it most</h2>
          <DataTable
            dense
            caption="Change in contribution per box"
            columns={[
              { key: "what", label: "If…" },
              { key: "eff", label: "Per box", align: "end" },
            ]}
            rows={moves}
          />
          <p className="text-sm leading-5 text-fg-secondary">
            Each row changes one assumption and keeps the rest.
          </p>
        </section>
      </div>

      <section
        aria-label="History"
        className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
      >
        <h2 className="text-[17px] leading-6 font-bold">History of changes</h2>
        <DataTable
          dense
          caption="Changes to the assumptions"
          columns={[
            { key: "at", label: "When" },
            { key: "by", label: "Who" },
            { key: "change", label: "What changed" },
          ]}
          rows={data.history.map((h, i) => ({
            id: String(i),
            at: dateTime.format(new Date(h.at)),
            by: h.by,
            change: h.change,
          }))}
          labels={{ empty: "No changes yet. The seed assumptions are in use." }}
        />
      </section>
    </div>
  );
}

/** "₹620" (no decimals when whole). @param {number} paise */
function formatRupees(paise) {
  return `₹${toDecimalText(paise)}`;
}
