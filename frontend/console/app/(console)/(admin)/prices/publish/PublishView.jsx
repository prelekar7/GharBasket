"use client";

import { Button, Dialog, EmptyState, formatPaise, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState, useTransition } from "react";
import { UploadIcon, WarnIcon } from "@/components/icons.jsx";
import { LinkButton } from "@/components/LinkButton.jsx";
import { formatDay } from "@/lib/catalog.js";
import { buildItems, compare, formatPct, parseCsv } from "@/lib/priceList.js";
import { publishPriceList } from "./actions.js";

const COLUMNS = [
  { key: "product", label: "Product" },
  { key: "step", label: "Step" },
  { key: "now", label: "Now", align: /** @type {const} */ ("end") },
  { key: "next", label: "New", align: /** @type {const} */ ("end") },
  { key: "change", label: "Change", align: /** @type {const} */ ("end") },
  { key: "note", label: "Note" },
];

/** One of the three counts above the table. @param {{ label: string, value: number, tone?: "danger" }} props */
function Stat({ label, value, tone }) {
  return (
    <div
      className={`flex min-w-40 flex-[1_1_160px] flex-col gap-0.5 rounded-md px-4 py-3.5 ${tone === "danger" ? "bg-danger-soft" : "bg-surface"}`}
    >
      <span className="text-sm leading-5 text-fg-secondary">{label}</span>
      <strong
        className={`text-2xl leading-[30px] tabular-nums ${tone === "danger" ? "text-danger" : ""}`}
      >
        {value}
      </strong>
    </div>
  );
}

/**
 * Publish a new price list (M2 US-3): pick the start date, upload the CSV, read what changes, then
 * publish after a confirm Dialog. Nothing here sets a price; the person's file does.
 * @param {{ hubName: string, known: import("@/lib/priceList.js").Known[], inUseSince: string | null, today: string }} props
 */
export function PublishView({ hubName, known, inUseSince, today }) {
  const router = useRouter();
  const fileInput = useRef(/** @type {HTMLInputElement | null} */ (null));
  const [date, setDate] = useState(today);
  const [file, setFile] = useState(/** @type {{ name: string, text: string } | null} */ (null));
  const [confirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState(
    /** @type {{ message: string, details: string[], productIds?: string[] } | null} */ (null),
  );
  const [publishing, startPublishing] = useTransition();
  /** One Idempotency-Key per confirmed publish; dropped when the date or file changes. */
  const key = useRef(/** @type {string | null} */ (null));

  const parsed = useMemo(() => (file ? parseCsv(file.text) : null), [file]);
  const result = useMemo(
    () => (parsed ? compare(parsed.lines, known, formatPaise) : null),
    [parsed, known],
  );
  const errors = result?.rows.filter((r) => r.state === "error") ?? [];
  const changes = result?.rows.filter((r) => r.state !== "error") ?? [];
  const dateOk = /^\d{4}-\d{2}-\d{2}$/.test(date) && date >= today;
  const canPublish =
    !!result && errors.length === 0 && changes.length > 0 && dateOk && !parsed?.fileProblem;

  /** @param {File | undefined} f */
  async function load(f) {
    if (!f) return;
    key.current = null;
    setFailure(null);
    setFile({ name: f.name, text: await f.text() });
  }

  function publish() {
    if (!parsed) return;
    key.current ??= crypto.randomUUID();
    const idempotencyKey = key.current;
    // The whole list: products the file does not name keep the price they have now.
    const items = buildItems(parsed.lines, known, parsed.hasMrp);
    startPublishing(async () => {
      const r = await publishPriceList(date, items, idempotencyKey).catch(() => ({
        ok: /** @type {const} */ (false),
        message: "That did not go through. Check your connection and try again.",
        details: [],
        productIds: [],
      }));
      if (!r.ok) {
        setFailure(r);
        return;
      }
      key.current = null;
      toast(
        r.status === "current"
          ? `New prices are in use at ${hubName}`
          : `Prices scheduled for ${formatDay(date)}`,
      );
      router.push(r.status === "current" ? "/prices" : "/prices?tab=scheduled");
    });
  }

  const tableRows = (result?.rows ?? []).map((r) => ({
    id: r.id || `row-${r.name}`,
    ...(r.state === "error" ? { __tone: /** @type {const} */ ("danger") } : {}),
    product: { text: r.name, strong: true },
    step: r.step,
    now: r.nowPaise === null ? "—" : { money: r.nowPaise },
    next: r.nextPaise === null ? "—" : { money: r.nextPaise },
    change: r.state === "error" && r.pct === null ? "—" : formatPct(r.pct),
    note: r.note,
  }));

  return (
    <>
      <div className="flex flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          Publish a new price list
        </h1>
        <p className="text-sm leading-5 text-fg-secondary">
          Upload the APMC morning rates as a CSV, check the changes, then publish for a date. Prices
          are per step, in rupees.{" "}
          {inUseSince
            ? `The list in use at ${hubName} started ${formatDay(inUseSince)}.`
            : `${hubName} has no price list yet.`}
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <TextField
          label="Starts on"
          type="date"
          description="Today or later"
          value={date}
          onChange={(v) => {
            key.current = null;
            setDate(v);
          }}
          isInvalid={!dateOk}
          errorMessage="Pick today or a later date"
          className="w-[220px]"
        />
        <input
          ref={fileInput}
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          tabIndex={-1}
          aria-label="Price list CSV file"
          onChange={(e) => {
            load(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <Button onPress={() => fileInput.current?.click()}>
          <UploadIcon />
          {file ? "Upload another CSV" : "Upload CSV"}
        </Button>
        {file && parsed && !parsed.fileProblem && (
          <p className="pb-3 text-sm leading-5 text-fg-secondary">
            {file.name} · {parsed.lines.length} rows
          </p>
        )}
      </div>

      {parsed?.fileProblem && (
        <p role="alert" className="rounded-md border border-danger bg-danger-soft px-3.5 py-3">
          {parsed.fileProblem}
        </p>
      )}

      {!result ? (
        <div className="rounded-md border border-line">
          <EmptyState
            title="Upload the morning rates"
            body="Columns: product_id and price in rupees per step, and optionally mrp. Download CSV on the Prices page gives a file in this shape to edit."
          />
        </div>
      ) : (
        !parsed?.fileProblem && (
          <>
            <div className="flex flex-wrap gap-3">
              <Stat label="Unchanged" value={result.same + result.untouched} />
              <Stat label="Changed" value={changes.length} />
              <Stat
                label="To fix before publishing"
                value={errors.length}
                tone={errors.length ? "danger" : undefined}
              />
            </div>

            {errors.length > 0 && (
              <section
                role="alert"
                className="flex items-start gap-2.5 rounded-md border border-danger bg-danger-soft px-3.5 py-3"
              >
                <span className="mt-0.5 text-danger">
                  <WarnIcon />
                </span>
                <div className="flex flex-col gap-1">
                  {errors.map((r) => (
                    <p key={`${r.id}-${r.note}`}>
                      {r.note.startsWith("Above MRP")
                        ? `${r.name} is ${formatPaise(/** @type {number} */ (r.nextPaise))}, above its MRP of ${formatPaise(/** @type {number} */ (r.mrpPaise))}.`
                        : r.note}
                    </p>
                  ))}
                  <p>Fix these in the CSV, then upload it again.</p>
                </div>
              </section>
            )}

            {tableRows.length > 0 ? (
              <DataTable
                caption="Prices that change or need fixing"
                columns={COLUMNS}
                rows={tableRows}
                footer={`Unchanged products are not listed.${result.untouched ? ` ${result.untouched} products are not in the file and keep their price.` : ""}`}
              />
            ) : (
              <div className="rounded-md border border-line">
                <EmptyState
                  title="Nothing changes"
                  body="Every price in this file is the same as the list in use, so there is nothing to publish."
                />
              </div>
            )}
          </>
        )
      )}

      {failure && (
        <section
          role="alert"
          className="flex flex-col gap-1 rounded-md border border-danger bg-danger-soft px-3.5 py-3"
        >
          <p className="font-semibold">{failure.message}</p>
          {failure.productIds?.length ? (
            <p>
              {failure.productIds
                .map((id) => known.find((k) => k.id === id)?.name ?? id)
                .join(", ")}
            </p>
          ) : null}
          {failure.details.map((d) => (
            <p key={d}>{d}</p>
          ))}
        </section>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <p className="max-w-[640px] text-sm leading-5 text-fg-secondary">
          Boxes planned from the start date use these prices. When an item jumps, Autopilot swaps it
          to keep families within their Budget Lock and tells them why.
        </p>
        <div className="flex gap-2">
          <LinkButton href="/prices" variant="quiet">
            Cancel
          </LinkButton>
          <Button
            variant="primary"
            isDisabled={!canPublish || publishing}
            onPress={() => {
              setFailure(null);
              setConfirming(true);
            }}
          >
            Publish for {dateOk ? formatDay(date) : "…"}
          </Button>
        </div>
      </div>

      <Dialog
        title={`Publish for ${formatDay(date)}?`}
        confirmLabel={`Publish ${changes.length} ${changes.length === 1 ? "change" : "changes"}`}
        cancelLabel="Keep editing"
        isOpen={confirming}
        onOpenChange={setConfirming}
        onConfirm={publish}
      >
        {changes.length} {changes.length === 1 ? "price changes" : "prices change"} at {hubName}.{" "}
        {date === today
          ? "They apply right away and replace the list in use, which stays in History."
          : `They start on ${formatDay(date)}; until then the list in use stays.`}
      </Dialog>
    </>
  );
}
