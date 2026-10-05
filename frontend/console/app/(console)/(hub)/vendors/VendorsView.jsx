"use client";

import { Button, EmptyState, Select, Sheet, TextField, Toaster, toast } from "@gharbasket/ui";
import { ChartCard, DataTable } from "@gharbasket/ui/console";
import { useEffect, useMemo, useState, useTransition } from "react";
import { plural, formatShortDate as shortDate } from "@/lib/format.js";
import { addVendor } from "./actions.js";

/**
 * GET /v1/hubs/{id}/vendors (M6 §3). `scores` and `history` come from the weekly scoring job; a vendor
 * with no deliveries yet has `scores: null`.
 * @typedef {{ id: string, name: string, supplies: string, kind: string, phone: string, item_count: number,
 *   scores: { ontime_pct: number, fill_pct: number, grade_a_pct: number, score: number,
 *     state: { tone: "primary" | "accent", label: string } } | null,
 *   history: { week: string, fill_pct: number, grade_a_pct: number }[] }} Vendor
 * @typedef {{ week: string, weeks: string[], items: Vendor[] }} VendorList
 */

const COLUMNS = [
  { key: "vendor", label: "Vendor", sortable: true },
  { key: "items", label: "Supplies" },
  { key: "ontime", label: "On time", align: /** @type {const} */ ("end"), sortable: true },
  { key: "fill", label: "Fill rate", align: /** @type {const} */ ("end"), sortable: true },
  { key: "gradeA", label: "Grade A", align: /** @type {const} */ ("end"), sortable: true },
  { key: "score", label: "Score", align: /** @type {const} */ ("end"), sortable: true },
  { key: "state", label: "State" },
];
const KINDS = [
  { value: "apmc", label: "APMC market trader" },
  { value: "fpo", label: "Farmer producer organisation (FPO)" },
  { value: "farmer", label: "Farmer" },
];

/** @param {number} n */
const pct = (n) => ({ text: `${n}%`, sort: n });

/** @param {{ list: VendorList }} props */
export function VendorsView({ list }) {
  const [vendors, setVendors] = useState(list.items);
  useEffect(() => setVendors(list.items), [list.items]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", supplies: "", kind: "", phone: "" });
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  const rows = vendors.map((v) => ({
    id: v.id,
    vendor: { text: v.name, sub: v.supplies, strong: true },
    items: plural(v.item_count, "item"),
    ontime: v.scores ? pct(v.scores.ontime_pct) : { text: "—", sort: -1 },
    fill: v.scores ? pct(v.scores.fill_pct) : { text: "—", sort: -1 },
    gradeA: v.scores ? pct(v.scores.grade_a_pct) : { text: "—", sort: -1 },
    score: v.scores
      ? { text: String(v.scores.score), sort: v.scores.score, strong: true }
      : { text: "—", sort: -1 },
    state: {
      badge: v.scores ? v.scores.state : { tone: /** @type {const} */ ("outline"), label: "New" },
    },
  }));

  const labels = list.weeks.map(shortDate);
  const series = useMemo(() => {
    const scored = vendors.filter((v) => v.history.length === list.weeks.length);
    return {
      fill: scored.map((v) => ({ name: v.name, values: v.history.map((h) => h.fill_pct) })),
      grade: scored.map((v) => ({ name: v.name, values: v.history.map((h) => h.grade_a_pct) })),
    };
  }, [vendors, list.weeks.length]);

  /** @param {keyof typeof form} field */
  const set = (field) => (/** @type {string} */ value) => {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  };

  function submit() {
    // The same checks the API makes, so most mistakes show before the round trip.
    /** @type {Record<string, string>} */
    const local = {};
    if (!form.name.trim()) local.name = "Enter the vendor’s name";
    if (!form.supplies.trim()) local.supplies = "Say what they supply";
    if (!form.kind) local.kind = "Choose the kind of vendor";
    if (!/^[6-9]\d{9}$/.test(form.phone)) local.phone = "Enter a 10-digit mobile number";
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    startTransition(async () => {
      const res = await addVendor(form, key);
      if (!res.ok) {
        setErrors(res.fields);
        toast(res.message);
        return;
      }
      setVendors((all) => [...all, res.data]);
      setOpen(false);
      setForm({ name: "", supplies: "", kind: "", phone: "" });
      setErrors({});
      setKey(crypto.randomUUID());
      toast(`${res.data.name} added`);
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">Vendors</h1>
          <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
            Weekly scores from receiving: did it come on time, in full, and at grade A. The plan
            suggests the best-scoring vendor per item.
          </p>
        </div>
        <Button onPress={() => setOpen(true)}>Add vendor</Button>
      </div>

      {vendors.length === 0 ? (
        <EmptyState
          title="No vendors yet"
          body="Add the people you buy from, and their scores will build up from receiving."
          action="Add vendor"
          onAction={() => setOpen(true)}
        />
      ) : (
        <DataTable
          columns={COLUMNS}
          rows={rows}
          defaultSort={{ key: "score", dir: "desc" }}
          caption={`Vendor scores, week of ${shortDate(list.week)}`}
          footer="Vendor names are placeholders until real vendors are onboarded."
        />
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 min-[900px]:grid-cols-2">
        <ChartCard
          title="Fill rate by week"
          subtitle="Share of ordered quantity delivered"
          kind="line"
          format="pct"
          max={100}
          labels={labels}
          series={series.fill}
        />
        <ChartCard
          title="Grade A share by week"
          subtitle="Share of received weight graded A"
          format="pct"
          max={100}
          labels={labels}
          series={series.grade}
        />
      </div>

      <Sheet
        title="Add a vendor"
        description="They appear on the procurement plan once their scores are in."
        open={open}
        onOpenChange={setOpen}
        footer={
          <>
            <Button onPress={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" isDisabled={busy} onPress={submit}>
              Add vendor
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <TextField
            label="Vendor name"
            value={form.name}
            onChange={set("name")}
            isInvalid={!!errors.name}
            errorMessage={errors.name}
          />
          <TextField
            label="What they supply"
            description="For example: vegetables, or dairy and eggs"
            value={form.supplies}
            onChange={set("supplies")}
            isInvalid={!!errors.supplies}
            errorMessage={errors.supplies}
          />
          <Select
            label="Kind of vendor"
            placeholder="Choose one"
            options={KINDS}
            value={form.kind}
            onChange={set("kind")}
            error={errors.kind}
          />
          <TextField
            label="Mobile number"
            prefix="+91"
            inputMode="numeric"
            value={form.phone}
            onChange={set("phone")}
            isInvalid={!!errors.phone}
            errorMessage={errors.phone}
          />
        </div>
      </Sheet>
      <Toaster />
    </div>
  );
}
