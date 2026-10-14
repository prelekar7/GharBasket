"use client";

import { Button, Checkbox, EmptyState, TextField, toast } from "@gharbasket/ui";
import { DataTable, FilterBar } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PlusIcon } from "@/components/icons.jsx";
import { LinkScope } from "@/components/LinkScope.jsx";
import { formatDay, todayIso } from "@/lib/catalog.js";
import { regionLabel, ruleSummary } from "@/lib/festivals.js";
import { saveFestival } from "./actions.js";

/** @typedef {import("@/lib/festivals.js").Festival} Festival */
/** @typedef {import("./actions.js").FestivalInput} FestivalInput */

const COLUMNS = [
  { key: "dates", label: "Dates", sortable: true },
  { key: "name", label: "Day", sortable: true },
  { key: "code", label: "Code" },
  { key: "region", label: "Region" },
  { key: "rule", label: "What the planner does" },
  { key: "verified", label: "Dates checked" },
];

const RULES = /** @type {const} */ ([
  ["fasting", "A fasting day", "Families who chose this fast get upvas food and no grains"],
  ["no_onion_garlic", "No onion or garlic", "Also left out of tiffins for these families"],
]);

/** "Sun 11 Oct to Mon 19 Oct", one day as "Thu 22 Oct". @param {Festival} f */
function datesText(f) {
  const start = formatDay(f.starts_on);
  return f.ends_on !== f.starts_on ? `${start} to ${formatDay(f.ends_on)}` : start;
}

/** Whether a day or run falls in the month "2026-10". @param {Festival} f @param {string} month */
const inMonth = (f, month) => f.starts_on.slice(0, 7) <= month && f.ends_on.slice(0, 7) >= month;

/**
 * Festival and fasting calendar (M2 US-5): the days the meal planner reads, for families who chose
 * that fast. Pick a day to edit its code and rules in the panel.
 * @param {{ festivals: Festival[], months: { value: string, label: string }[] }} props
 */
export function FestivalsView({ festivals, months }) {
  const [region, setRegion] = useQueryState("region", parseAsString.withDefault("maharashtra"));
  const [month, setMonth] = useQueryState("month", parseAsString.withDefault(""));
  const [show, setShow] = useQueryState("show", parseAsString.withDefault(""));
  const [edit, setEdit] = useQueryState("edit", parseAsString.withDefault(""));

  const shown = festivals
    .filter((f) => region === "all" || f.region === "all" || f.region === region)
    .filter((f) => !month || inMonth(f, month))
    .filter((f) => show !== "fasting" || f.rules.fasting);
  const selected = edit === "new" ? null : (shown.find((f) => f.id === edit) ?? shown[0] ?? null);
  const adding = edit === "new";

  const rows = shown.map((f) => ({
    id: f.id,
    dates: { text: datesText(f), sort: f.starts_on },
    name: { text: f.name, strong: true, href: `?edit=${encodeURIComponent(f.id)}` },
    code: f.code,
    region: regionLabel(f.region),
    rule: ruleSummary(f.rules),
    verified: f.verified
      ? { badge: { tone: "primary", label: "Checked" } }
      : { badge: { tone: "outline", label: "Not checked yet" } },
  }));
  const rangeLabel =
    months.length === 2 && !month
      ? `${months[0].label} to ${months[1].label}`
      : (months.find((m) => m.value === month)?.label ?? "");

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex max-w-3xl flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            Festival and fasting calendar
          </h1>
          <p className="text-sm leading-5 text-fg-secondary">
            The meal planner reads these days and their rules, but only for families who chose that
            fast in their food settings. Nobody gets upvas food they didn’t ask for.
          </p>
        </div>
        <Button variant="primary" onPress={() => setEdit("new")}>
          <PlusIcon />
          Add a day
        </Button>
      </div>

      <FilterBar
        filters={[
          {
            key: "region",
            label: "Region",
            value: region,
            options: [
              { value: "maharashtra", label: "Maharashtra" },
              { value: "all", label: "All regions" },
            ],
          },
          {
            key: "month",
            label: "Month",
            value: month,
            options: [{ value: "", label: "Both months" }, ...months],
          },
          {
            key: "show",
            label: "Show",
            value: show,
            options: [
              { value: "", label: "All days" },
              { value: "fasting", label: "Fasting days only" },
            ],
          },
        ]}
        onFilterChange={(key, value) => {
          const set = { region: setRegion, month: setMonth, show: setShow }[
            /** @type {"region"} */ (key)
          ];
          set(value === "" || (key === "region" && value === "maharashtra") ? null : value);
        }}
        active={show === "fasting" ? [{ key: "show", label: "Fasting days only" }] : []}
        onRemove={() => setShow(null)}
        onClear={() => setShow(null)}
        count={shown.length}
      />

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        {rows.length === 0 ? (
          <div className="rounded-md border border-line">
            <EmptyState
              title="No days in this view"
              body="Change the region, month or filter, or add a day."
            />
          </div>
        ) : (
          <LinkScope>
            <DataTable
              caption={`Festival and fasting days, ${rangeLabel}`}
              columns={COLUMNS}
              rows={rows}
              defaultSort={{ key: "dates", dir: "asc" }}
              footer="Days not checked yet are placeholders to verify against the panchang."
            />
          </LinkScope>
        )}
        {(selected || adding) && (
          <FestivalPanel
            key={selected?.id ?? "new"}
            festival={adding ? null : selected}
            onSaved={(f) => setEdit(f.id)}
          />
        )}
      </div>
    </>
  );
}

/** @param {Festival | null} f @returns {FestivalInput} */
const toInput = (f) => ({
  code: f?.code ?? "",
  name: f?.name ?? "",
  region: f?.region ?? "all",
  starts_on: f ? f.starts_on : todayIso(),
  ends_on: f ? f.ends_on : todayIso(),
  rules: f?.rules ?? { fasting: false, no_onion_garlic: false },
  verified: f?.verified ?? false,
});

/**
 * The edit panel of one day or run (or a new one). Saving sends an Idempotency-Key made for this save
 * and offers Undo; what you typed stays if the save fails. The calendar's admin endpoints have not
 * shipped (they are a @proposal), so against the real API a save says "not available yet".
 * @param {{ festival: Festival | null, onSaved: (f: Festival) => void }} props
 */
function FestivalPanel({ festival, onSaved }) {
  const router = useRouter();
  const [form, setForm] = useState(() => toInput(festival));
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [failure, setFailure] = useState("");
  const [saving, startSaving] = useTransition();
  const key = useRef(/** @type {string | null} */ (null));

  /** @param {Partial<FestivalInput>} patch */
  function change(patch) {
    key.current = null;
    setForm((f) => ({ ...f, ...patch }));
  }

  function onSave() {
    /** @type {Record<string, string>} */
    const e = {};
    if (!form.name.trim()) e.name = "Give the day a name";
    if (!/^[a-z][a-z0-9_]{1,39}$/.test(form.code))
      e.code = "Use 2 to 40 lower-case letters, digits or _";
    if (!form.starts_on) e.starts_on = "Pick the first day";
    if (!form.ends_on) e.ends_on = "Pick the last day";
    else if (form.starts_on && form.ends_on < form.starts_on)
      e.ends_on = "The last day cannot be before the first";
    setErrors(e);
    if (Object.keys(e).length) return;
    key.current ??= crypto.randomUUID();
    const idempotencyKey = key.current;
    startSaving(async () => {
      const r = await saveFestival(
        festival?.id ?? null,
        { ...form, name: form.name.trim() },
        idempotencyKey,
      ).catch(() => ({
        ok: /** @type {const} */ (false),
        message: "That did not go through. Check your connection and try again.",
        fields: /** @type {Record<string, string>} */ ({}),
      }));
      if (!r.ok) {
        setErrors(r.fields);
        setFailure(r.message);
        return;
      }
      key.current = null;
      setFailure("");
      const before = festival;
      toast(`${r.festival.name} saved`, {
        action: before
          ? {
              label: "Undo",
              onClick: async () => {
                const u = await saveFestival(r.festival.id, toInput(before), crypto.randomUUID());
                toast(u.ok ? "Change undone" : "Could not undo. Edit the day to change it back.");
                router.refresh();
              },
            }
          : undefined,
      });
      onSaved(r.festival);
      router.refresh();
    });
  }

  const title = festival ? `${festival.name} · ${regionLabel(festival.region)}` : "Add a day";

  return (
    <section
      aria-label={festival ? `Edit ${festival.name}` : "Add a day"}
      className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
    >
      <h2 className="text-[17px] leading-6 font-bold">{title}</h2>
      {failure && (
        <p
          role="alert"
          className="rounded-md border border-danger bg-danger-soft px-3 py-2 text-sm"
        >
          {failure}
        </p>
      )}
      <TextField
        label="Name"
        value={form.name}
        onChange={(name) => change({ name })}
        isInvalid={!!errors.name}
        errorMessage={errors.name}
      />
      <TextField
        label="Code"
        description="What the planner reads: navratri, ekadashi. It cannot change later."
        value={form.code}
        onChange={(code) => change({ code })}
        isDisabled={!!festival}
        isInvalid={!!errors.code}
        errorMessage={errors.code}
      />
      <div className="flex flex-wrap gap-3">
        <TextField
          label="Starts on"
          type="date"
          value={form.starts_on}
          onChange={(starts_on) => change({ starts_on })}
          isInvalid={!!errors.starts_on}
          errorMessage={errors.starts_on}
          className="min-w-40 flex-1"
        />
        <TextField
          label="Ends on"
          type="date"
          value={form.ends_on}
          onChange={(ends_on) => change({ ends_on })}
          isInvalid={!!errors.ends_on}
          errorMessage={errors.ends_on}
          className="min-w-40 flex-1"
        />
      </div>
      <Checkbox
        label="Dates checked against the panchang"
        isSelected={form.verified}
        onChange={(verified) => change({ verified })}
      />
      <p className="text-[15px] leading-[22px] font-bold">Rules for the planner</p>
      {RULES.map(([rule, label, description]) => (
        <Checkbox
          key={rule}
          label={label}
          description={description}
          isSelected={form.rules[rule]}
          onChange={(on) => change({ rules: { ...form.rules, [rule]: on } })}
        />
      ))}
      <Button variant="primary" block isDisabled={saving} onPress={onSave}>
        Save · applies to plans made after now
      </Button>
    </section>
  );
}
