"use client";

import { Button, SegmentedControl, Select, Tabs, TextField, Toaster, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useEffect, useRef, useState, useTransition } from "react";
import { formatDay } from "@/lib/format.js";
import { saveReceipt } from "./actions.js";

/**
 * GET /v1/hubs/{id}/receiving?date= (our proposal; M6 only specifies the POST .../grn). A line is one
 * item on a vendor's purchase order.
 * @typedef {{ vendor_id: string, product_id: string, name: string, unit: string, ordered_qty: number,
 *   needed_today: number, received_qty: number | null, grade: "A" | "B" | "C" | null,
 *   wasted_kg: number | null, note: string | null, expires_on: string | null,
 *   status: "pending" | "received" | "rejected" }} ReceivingLine
 * @typedef {{ date: string, as_of: string, vendors: { id: string, name: string, supplies: string, line_count: number }[],
 *   lines: ReceivingLine[] }} ReceivingBoard
 */

const GRADES = [
  { value: "A", label: "A · good" },
  { value: "B", label: "B · cook soon" },
  { value: "C", label: "C · reject" },
];
const COLUMNS = [
  { key: "item", label: "Item" },
  { key: "ordered", label: "Ordered", align: /** @type {const} */ ("end") },
  { key: "received", label: "Received", align: /** @type {const} */ ("end") },
  { key: "grade", label: "Grade" },
  { key: "expiry", label: "Expires" },
  { key: "state", label: "State" },
];
/** Unit words for the receiving form; anything else is shown as the unit itself. */
const PLURAL = /** @type {Record<string, string>} */ ({
  bunch: "bunches",
  pack: "packs",
  dozen: "dozen",
});
const LABEL = /** @type {Record<string, string>} */ ({
  bunch: "Bunches",
  kg: "Kilograms",
  pack: "Packs",
  dozen: "Dozens",
});

/** @param {number} n @param {string} unit */
const withUnit = (n, unit) => `${n} ${n === 1 ? unit : (PLURAL[unit] ?? unit)}`;

/** @param {{ board: ReceivingBoard }} props */
export function ReceivingView({ board }) {
  const [lines, setLines] = useState(board.lines);
  useEffect(() => setLines(board.lines), [board.lines]);

  // Open the vendor whose delivery is in progress (some lines in, some waiting), else the first with work left.
  const waiting = (/** @type {string} */ id) =>
    lines.filter((l) => l.vendor_id === id && l.status === "pending");
  const started = board.vendors.find(
    (v) => waiting(v.id).length && waiting(v.id).length < v.line_count,
  );
  const [vendor, setVendor] = useQueryState(
    "vendor",
    parseAsString.withDefault(
      (started ?? board.vendors.find((v) => waiting(v.id).length) ?? board.vendors[0]).id,
    ),
  );
  const [pick, setPick] = useState(/** @type {string | null} */ (null));
  const [qty, setQty] = useState("");
  const [grade, setGrade] = useState("A");
  const [photo, setPhoto] = useState(/** @type {string | null} */ (null));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();
  const fileInput = useRef(/** @type {HTMLInputElement | null} */ (null));

  const own = lines.filter((l) => l.vendor_id === vendor);
  const pending = own.filter((l) => l.status === "pending");
  const current = pending.find((l) => l.product_id === pick) ?? pending[0] ?? null;
  const activeVendor = board.vendors.find((v) => v.id === vendor);

  const rows = own.map((l) => ({
    id: l.product_id,
    item: { text: l.name, strong: true },
    ordered: withUnit(l.ordered_qty, l.unit),
    received: l.received_qty ?? "—",
    grade: l.grade
      ? {
          text: l.wasted_kg ? `${l.grade} · ${l.wasted_kg} kg wilted` : l.grade,
          sub: l.note ?? undefined,
        }
      : "—",
    expiry: l.expires_on && l.status === "received" ? formatDay(l.expires_on) : "—",
    state: {
      badge:
        l.status === "received"
          ? { tone: /** @type {const} */ ("primary"), label: "Received" }
          : l.status === "rejected"
            ? { tone: /** @type {const} */ ("danger"), label: "Rejected" }
            : l.product_id === current?.product_id
              ? { tone: /** @type {const} */ ("accent"), label: "Weighing" }
              : { tone: /** @type {const} */ ("outline"), label: "Not yet" },
    },
  }));

  const entered = qty.trim() === "" ? null : Number(qty);
  const qtyOk = entered !== null && Number.isInteger(entered) && entered >= 0 && entered <= 999;
  const short =
    current && qtyOk ? Math.max(0, current.needed_today - (grade === "C" ? 0 : entered)) : 0;

  /** Back to a clean form for the next line. */
  function reset() {
    setQty("");
    setGrade("A");
    setPhoto(null);
    setPick(null);
    setKey(crypto.randomUUID());
  }

  function save() {
    if (!current || !qtyOk) return;
    startTransition(async () => {
      const res = await saveReceipt(
        {
          productId: current.product_id,
          receivedQty: entered ?? 0,
          grade,
          hasPhoto: photo !== null,
        },
        key,
      );
      if (!res.ok) {
        toast(res.message);
        return;
      }
      const { line, shortage } = res.data;
      setLines((all) => all.map((l) => (l.product_id === line.product_id ? line : l)));
      toast(
        shortage
          ? `${line.name} saved. ${withUnit(shortage.short_by, shortage.unit)} short went to packing.`
          : `${line.name} saved.`,
      );
      reset();
    });
  }

  const form = (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <DataTable
        columns={COLUMNS}
        rows={rows}
        caption={`Lines on the ${activeVendor?.name ?? "vendor"} order`}
        labels={{ empty: "This order has no lines." }}
      />
      <section
        aria-label="Receive a line"
        className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
      >
        {current ? (
          <>
            <h2 className="m-0 text-[17px] leading-6 font-bold">Receive: {current.name}</h2>
            <p className="m-0 text-sm text-fg-secondary">
              Ordered {withUnit(current.ordered_qty, current.unit)}. Families need{" "}
              {current.needed_today} today.
            </p>
            {pending.length > 1 && (
              <Select
                label="Line to receive"
                value={current.product_id}
                onChange={(v) => {
                  setPick(v);
                  setQty("");
                  setKey(crypto.randomUUID());
                }}
                options={pending.map((l) => ({ value: l.product_id, label: l.name }))}
              />
            )}
            <TextField
              label={`${LABEL[current.unit] ?? `Received (${current.unit})`} received`}
              inputMode="numeric"
              value={qty}
              onChange={setQty}
              isInvalid={entered !== null && !qtyOk}
              errorMessage="Use a whole number from 0 to 999"
            />
            <SegmentedControl label="Grade" options={GRADES} value={grade} onChange={setGrade} />
            {short > 0 && (
              <div className="flex flex-col gap-1.5 rounded-sm bg-danger-soft p-3">
                <strong>{withUnit(short, current.unit)} short for today’s bags</strong>
                <span className="text-sm">
                  Saving this sends the shortage to packing. Each family’s own setting decides: a
                  swap they allowed, or money back.
                </span>
              </div>
            )}
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              capture="environment"
              className="sr-only"
              tabIndex={-1}
              aria-label="Photo of the delivery"
              onChange={(e) => setPhoto(e.target.files?.[0]?.name ?? null)}
            />
            <Button onPress={() => fileInput.current?.click()}>
              {photo ? `Photo added: ${photo}` : "Add a photo"}
            </Button>
            <Button variant="primary" block isDisabled={!qtyOk || busy} onPress={save}>
              {grade === "C"
                ? "Save line · rejected"
                : `Save line${current.expires_on ? ` · expires ${formatDay(current.expires_on)}` : ""}`}
            </Button>
          </>
        ) : (
          <>
            <h2 className="m-0 text-[17px] leading-6 font-bold">Everything is in</h2>
            <p className="m-0 text-sm text-fg-secondary">
              Every line on the {activeVendor?.name} order has been received and graded.
            </p>
          </>
        )}
      </section>
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">
          Receiving and QC · {formatDay(board.date)}
        </h1>
        <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
          {board.as_of}. Weigh each line, grade it, and the receipt (GRN) creates stock lots with
          expiry. Grades feed the weekly vendor scores.
        </p>
      </div>
      <Tabs
        label="Purchase orders"
        value={vendor}
        onChange={(v) => {
          setVendor(v);
          setPick(null);
          setQty("");
        }}
        tabs={board.vendors.map((v) => ({
          value: v.id,
          label: `${v.name} · ${v.supplies.toLowerCase()}`,
          count: v.line_count,
        }))}
      >
        {form}
      </Tabs>
      <Toaster />
    </div>
  );
}
