"use client";

import { Button, Dialog, Select, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useState, useTransition } from "react";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { parseDecimal, toDecimalText } from "@/lib/money.js";
import { updateHub } from "./actions.js";

/** @typedef {import("./data.js").Hub} Hub */

/** The four times that are the hub's own (`timings`), in the order the API requires. */
const DAY = /** @type {const} */ ([
  { key: "receiving", label: "Goods receiving starts" },
  { key: "packing", label: "Packing tasks are made" },
  { key: "dispatch", label: "Riders leave the hub" },
  { key: "surplus", label: "Surplus cascade runs" },
]);

const STATUS = [
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
];

/**
 * The other scheduled jobs of a delivery day (T), as the handbook lists them (10.4). They are fixed
 * for every hub and not stored anywhere the console can change, so this is copy, not data.
 */
const FIXED_JOBS = [
  ["draft_weeks", "Draft weeks generated", "T-3 · 6:00 PM", "subscriptions"],
  ["approve_reminder", "Reminder to approve", "T-1 · 7:00 PM", "subscriptions"],
  ["cutoff", "Cut-off: orders lock", "T-1 · 9:00 PM", "orders"],
  ["procurement", "Procurement plan", "T-1 · 9:10 PM", "ops"],
  ["tiffins_assigned", "Kitchen tiffins assigned", "T-1 · 9:30 PM", "partners"],
  ["topup_grace", "Top-up grace ends", "T-1 · 11:00 PM", "orders"],
  ["routes", "Routes solved", "T-1 · 11:15 PM", "logistics"],
  ["payouts", "Partner payouts", "Mondays · 10:00 AM", "billing"],
  ["retrain", "Models retrained", "Sundays · 3:00 AM", "training"],
].map(([key, what, when, owner]) => ({ key, what, when, owner }));

/** The form's text for a hub: money as rupees, the buffer as digits. @param {Hub} hub */
const draftOf = (hub) => ({
  address: hub.address,
  status: hub.status,
  buffer: String(hub.procurement_buffer_pct),
  fee: toDecimalText(hub.pre_live_box_fee_paise),
  ...hub.timings,
});

/**
 * Hub settings (M6 §3.1): the hub's day, its status and address, the procurement buffer and the delivery
 * fee before a society is live. Admin only. Nothing is saved until "Save changes"; the change is sent
 * with the version this screen read, so it cannot overwrite someone else's. Pausing a hub stops its
 * jobs, so it asks first.
 * @param {{ hub: Hub }} props
 */
export function SettingsView({ hub }) {
  const [draft, setDraft] = useState(() => draftOf(hub));
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [confirmPause, setConfirmPause] = useState(false);
  const [busy, startSaving] = useTransition();

  /** @param {keyof typeof draft} field */
  const set = (field) => (/** @type {string} */ value) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  };

  /**
   * What differs from the hub, as a HubPatch, or the messages for the fields that are not valid.
   * @returns {{ change: import("./data.js").HubChange } | { invalid: Record<string, string> }}
   */
  function diff() {
    /** @type {import("./data.js").HubChange} */
    const change = {};
    /** @type {Record<string, string>} */
    const invalid = {};
    const address = draft.address.trim();
    if (address.length < 1 || address.length > 300)
      invalid.address = "Enter the address (up to 300 characters)";
    else if (address !== hub.address) change.address = address;
    if (draft.status !== hub.status) change.status = draft.status;
    const buffer = /^\d{1,2}$/.test(draft.buffer.trim()) ? Number(draft.buffer) : -1;
    if (buffer < 0 || buffer > 50)
      invalid.procurement_buffer_pct = "Enter a whole number from 0 to 50";
    else if (buffer !== hub.procurement_buffer_pct) change.procurement_buffer_pct = buffer;
    const fee = parseDecimal(draft.fee, 2);
    if (fee === null || fee > 10_000)
      invalid.pre_live_box_fee_paise = "Enter an amount from ₹0 to ₹100";
    else if (fee !== hub.pre_live_box_fee_paise) change.pre_live_box_fee_paise = fee;
    for (const { key, label } of DAY) {
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft[key]))
        invalid[key] = `Choose a time for “${label}”`;
      else if (draft[key] !== hub.timings[key])
        change.timings = { ...change.timings, [key]: draft[key] };
    }
    return Object.keys(invalid).length ? { invalid } : { change };
  }

  const checked = diff();
  const changed = "change" in checked && Object.keys(checked.change).length > 0;

  /** @param {import("./data.js").HubChange} change */
  function save(change) {
    startSaving(async () => {
      const result = await updateHub(hub.version, change);
      if (result.ok) toast("Settings saved.");
      else {
        setErrors(result.fields);
        toast(result.message);
      }
    });
  }

  function submit() {
    const next = diff();
    if ("invalid" in next) {
      setErrors(next.invalid);
      return;
    }
    if (next.change.status === "paused") setConfirmPause(true);
    else save(next.change);
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={`Settings · ${hub.name}`}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              isDisabled={busy || !changed}
              onPress={() => {
                setDraft(draftOf(hub));
                setErrors({});
              }}
            >
              Discard changes
            </Button>
            <Button variant="primary" isDisabled={busy || !changed} onPress={submit}>
              Save changes
            </Button>
          </div>
        }
      >
        The hub’s day and the numbers that shape its boxes. Admin only; every change is logged with
        who made it and when.
      </PageHeader>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section
          aria-label="The delivery-day clock"
          className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
        >
          <h2 className="text-[17px] leading-6 font-bold">The hub’s day</h2>
          <p className="text-sm leading-5 text-fg-secondary">
            Times in India, in this order. They plan the hub’s jobs and are shown to the team; the
            cut-off is not one of them.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {DAY.map(({ key, label }) => (
              <TextField
                key={key}
                type="time"
                label={label}
                value={draft[key]}
                onChange={set(key)}
                isInvalid={!!errors[key]}
                errorMessage={errors[key]}
              />
            ))}
          </div>
          <h2 className="pt-2 text-[17px] leading-6 font-bold">Fixed job times</h2>
          <DataTable
            dense
            caption="Scheduled jobs around a delivery day (T), the same for every hub"
            columns={[
              { key: "what", label: "What happens" },
              { key: "when", label: "When" },
              { key: "owner", label: "Runs in" },
            ]}
            rows={FIXED_JOBS.map((j) => ({
              id: j.key,
              what: { text: j.what, strong: true },
              when: j.when,
              owner: j.owner,
            }))}
          />
        </section>

        <section
          aria-label="This hub"
          className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
        >
          <h2 className="text-[17px] leading-6 font-bold">This hub</h2>
          <Select
            label="Status"
            description="A paused hub runs no jobs."
            options={STATUS}
            value={draft.status}
            onChange={set("status")}
            error={errors.status}
          />
          <TextField
            label="Address"
            value={draft.address}
            onChange={set("address")}
            isInvalid={!!errors.address}
            errorMessage={errors.address}
          />
          <TextField
            label="Procurement buffer"
            description="Extra bought on top of the forecast, as a share of it."
            prefix="%"
            inputMode="numeric"
            value={draft.buffer}
            onChange={set("buffer")}
            isInvalid={!!errors.procurement_buffer_pct}
            errorMessage={errors.procurement_buffer_pct}
          />
          <TextField
            label="Delivery fee on each box before a society is live"
            prefix="₹"
            inputMode="decimal"
            value={draft.fee}
            onChange={set("fee")}
            isInvalid={!!errors.pre_live_box_fee_paise}
            errorMessage={errors.pre_live_box_fee_paise}
          />
        </section>
      </div>

      <Dialog
        title="Pause this hub?"
        confirmLabel="Pause hub"
        cancelLabel="Keep it active"
        isOpen={confirmPause}
        onOpenChange={setConfirmPause}
        onConfirm={() => {
          const next = diff();
          if ("change" in next) save(next.change);
        }}
      >
        No jobs run for a paused hub: no cut-off, packing or routes. Only pause it when no family is
        waiting on a delivery from it.
      </Dialog>
    </div>
  );
}
