"use client";

import {
  Button,
  Checkbox,
  EmptyState,
  Select,
  Sheet,
  TextField,
  Toaster,
  toast,
} from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { addVendor, updateVendor } from "./actions.js";

/**
 * GET /v1/hubs/{id}/vendors (M6 §3.1, built). `score` is the last weekly score from 0 to 1, null until
 * the vendor has been scored. The contract has no breakdown of the score and no "what they supply"
 * beyond `notes`.
 * @typedef {{ id: string, name: string, kind: "apmc_trader" | "fpo" | "farmer" | "wholesaler",
 *   phone: string | null, whatsapp: boolean, is_default: boolean, notes: string | null, active: boolean,
 *   score: number | null, version: number }} Vendor
 */

const COLUMNS = [
  { key: "vendor", label: "Vendor", sortable: true },
  { key: "kind", label: "Kind" },
  { key: "phone", label: "Phone" },
  { key: "score", label: "Score", align: /** @type {const} */ ("end"), sortable: true },
  { key: "state", label: "State" },
];
const KINDS = [
  { value: "apmc_trader", label: "APMC market trader" },
  { value: "fpo", label: "Farmer producer organisation (FPO)" },
  { value: "farmer", label: "Farmer" },
  { value: "wholesaler", label: "Wholesaler" },
];
const BLANK = { name: "", notes: "", kind: "", phone: "", whatsapp: true, isDefault: false };

/** @param {string | null} phone `+919800002001` as `+91 98000 02001` */
const showPhone = (phone) => (phone ? phone.replace(/^(\+91)(\d{5})(\d+)$/, "$1 $2 $3") : "—");

/** @param {Vendor} v */
function state(v) {
  if (!v.active) return { tone: /** @type {const} */ ("outline"), label: "Inactive" };
  if (v.is_default) return { tone: /** @type {const} */ ("primary"), label: "Default" };
  return v.score === null
    ? { tone: /** @type {const} */ ("outline"), label: "New" }
    : { tone: /** @type {const} */ ("neutral"), label: "Active" };
}

/** @param {{ vendors: Vendor[] }} props */
export function VendorsView({ vendors }) {
  const router = useRouter();
  const [selected, setSelected] = useState(/** @type {(string | number)[]} */ ([]));
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();
  // Forget a selection whose vendor is gone after a reload.
  useEffect(
    () => setSelected((s) => s.filter((id) => vendors.some((v) => v.id === id))),
    [vendors],
  );

  const rows = vendors.map((v) => ({
    id: v.id,
    vendor: { text: v.name, sub: v.notes ?? undefined, strong: true },
    kind: KINDS.find((k) => k.value === v.kind)?.label ?? v.kind,
    phone: { text: showPhone(v.phone), sub: v.phone && v.whatsapp ? "WhatsApp" : undefined },
    score:
      v.score === null
        ? { text: "—", sort: -1 }
        : { text: String(Math.round(v.score * 100)), sort: v.score, strong: true },
    state: { badge: state(v) },
  }));
  const one = selected.length === 1 ? vendors.find((v) => v.id === selected[0]) : undefined;

  /** @param {keyof typeof BLANK} field */
  const set = (field) => (/** @type {string | boolean} */ value) => {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  };

  function submit() {
    // The same checks the API makes, so most mistakes show before the round trip.
    /** @type {Record<string, string>} */
    const local = {};
    const digits = form.phone.replace(/\s/g, "");
    if (form.name.trim().length < 2) local.name = "Enter the vendor’s name";
    if (!form.kind) local.kind = "Choose the kind of vendor";
    if (digits && !/^[6-9]\d{9}$/.test(digits)) local.phone = "Enter a 10-digit mobile number";
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    startTransition(async () => {
      const res = await addVendor(
        {
          name: form.name.trim(),
          kind: form.kind,
          phone: digits ? `+91${digits}` : null,
          whatsapp: form.whatsapp,
          is_default: form.isDefault,
          notes: form.notes.trim() || null,
        },
        key,
      );
      if (!res.ok) {
        setErrors(res.fields);
        toast(res.message);
        return;
      }
      setOpen(false);
      setForm(BLANK);
      setErrors({});
      setKey(crypto.randomUUID());
      toast(`${res.data.name} added`);
      router.refresh(); // a new default changes the old default too, so read the list again
    });
  }

  /** @param {Vendor} vendor @param {{ is_default?: boolean, active?: boolean }} patch @param {string} done */
  function change(vendor, patch, done) {
    startTransition(async () => {
      const res = await updateVendor(vendor.id, vendor.version, patch);
      if (!res.ok) {
        toast(res.message);
        return;
      }
      toast(`${vendor.name} ${done}`);
      router.refresh();
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">Vendors</h1>
          <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
            The people you buy from. The weekly score comes from receiving: freshness, price,
            reliability and defects. The default vendor is suggested for items you have never
            bought.
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
          caption="Vendors and their latest weekly score"
          selectable
          selected={selected}
          onSelect={setSelected}
          labels={{ selectRow: "Select {row}" }}
          bulk={
            one ? (
              <span className="flex flex-wrap items-center gap-2">
                <Button
                  isDisabled={busy || one.is_default || !one.active}
                  onPress={() => change(one, { is_default: true }, "is now the default")}
                >
                  Make default
                </Button>
                <Button
                  isDisabled={busy}
                  onPress={() =>
                    change(one, { active: !one.active }, one.active ? "deactivated" : "reactivated")
                  }
                >
                  {one.active ? "Deactivate" : "Reactivate"}
                </Button>
              </span>
            ) : (
              <span>Select one vendor to make it the default or to deactivate it.</span>
            )
          }
          footer="Vendors are deactivated, never deleted, so old purchase orders keep their names."
        />
      )}

      <Sheet
        title="Add a vendor"
        description="They can be put on procurement lines and purchase orders straight away."
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
            label="What they supply (optional)"
            description="For example: vegetables, or dairy and eggs"
            value={form.notes}
            onChange={set("notes")}
            isInvalid={!!errors.notes}
            errorMessage={errors.notes}
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
            label="Mobile number (optional)"
            prefix="+91"
            inputMode="numeric"
            value={form.phone}
            onChange={set("phone")}
            isInvalid={!!errors.phone}
            errorMessage={errors.phone}
          />
          <Checkbox
            label="Send purchase orders on WhatsApp"
            isSelected={form.whatsapp}
            onChange={set("whatsapp")}
          />
          <Checkbox
            label="Make this the default vendor"
            description="Suggested for items with no purchase history"
            isSelected={form.isDefault}
            onChange={set("isDefault")}
          />
        </div>
      </Sheet>
      <Toaster />
    </div>
  );
}
