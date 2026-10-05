"use client";

import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  formatPaise,
  Select,
  Sheet,
  Switch,
  TextField,
  toast,
} from "@gharbasket/ui";
import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { PlusIcon } from "@/components/icons.jsx";
import { rupeesToPaise } from "@/lib/priceList.js";
import { savePlan } from "./actions.js";

/** @typedef {import("@/mocks/plans.js").Plan} Plan */

const KIND_LABEL = {
  autopilot: "weekly box",
  tiffin: "home-cooked meals",
  salad: "cut-salad add-on",
};
const KIND_OPTIONS = [
  { value: "autopilot", label: "Autopilot (weekly box)" },
  { value: "tiffin", label: "Tiffin (home-cooked meals)" },
  { value: "salad", label: "Salad (cut-salad add-on)" },
];

/**
 * Plans (M3): what a household can subscribe to. Cards for each plan; Edit plan and Add plan open
 * a bottom sheet. Changing a monthly fee is a money change, so it asks to confirm first.
 * @param {{ plans: Plan[] }} props
 */
export function PlansView({ plans }) {
  const [target, setTarget] = useState(/** @type {Plan | "new" | null} */ (null));
  const [opened, setOpened] = useState(0);
  const [open, setOpen] = useState(false);

  /** @param {Plan | "new"} t */
  function show(t) {
    setTarget(t);
    setOpened((n) => n + 1);
    setOpen(true);
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex max-w-3xl flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">Plans</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            What a household can subscribe to: the weekly Autopilot box, tiffins and, later, salads.
            Each plan has a code, a monthly fee and its features.
          </p>
        </div>
        <Button onPress={() => show("new")}>
          <PlusIcon />
          Add plan
        </Button>
      </div>

      {plans.length === 0 ? (
        <div className="rounded-md border border-line">
          <EmptyState title="No plans yet" body="Add the first plan to let households subscribe." />
        </div>
      ) : (
        <div className="grid items-start gap-4 min-[700px]:grid-cols-2 min-[1100px]:grid-cols-3">
          {plans.map((p) => (
            <section
              key={p.code}
              aria-label={p.name}
              className="flex min-w-0 flex-col gap-2.5 rounded-md bg-surface p-[18px]"
            >
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-[17px] leading-6 font-bold">{p.name}</h2>
                <Badge tone={p.state === "live" ? "primary" : "outline"}>
                  {p.state === "live" ? "Live" : "Draft"}
                </Badge>
              </div>
              <span className="text-sm leading-5 text-fg-secondary">
                {p.code} · {KIND_LABEL[p.kind]}
              </span>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm leading-5 text-fg-secondary">Monthly fee</span>
                <strong>{p.fee_paise === null ? "Not set" : formatPaise(p.fee_paise)}</strong>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm leading-5 text-fg-secondary">Subscribers</span>
                <strong>{p.subscriber_count ?? "—"}</strong>
              </div>
              <ul className="m-0 list-disc pl-[18px] text-sm leading-[22px]">
                {p.features.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
              <Button block onPress={() => show(p)}>
                Edit plan
              </Button>
            </section>
          ))}
        </div>
      )}
      <p className="text-sm leading-5 text-fg-secondary">
        Fees stay “Not set” until the team decides pricing. A subscriber count of “—” means that
        plan’s subscriptions are not tracked yet.
      </p>

      {target && (
        <PlanSheet
          key={opened}
          plan={target === "new" ? null : target}
          open={open}
          onOpenChange={setOpen}
        />
      )}
    </>
  );
}

/** @param {{ id: string, label: string, value: string, onChange: (v: string) => void, description?: string, error?: string }} props */
function TextArea({ id, label, value, onChange, description, error }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm leading-5 font-semibold">
        {label}
      </label>
      {description && <p className="text-sm leading-5 text-fg-secondary">{description}</p>}
      <textarea
        id={id}
        rows={4}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={!!error}
        className={`w-full rounded-sm border bg-surface px-3 py-2.5 text-base text-fg outline-none focus-visible:shadow-focus ${error ? "border-2 border-danger" : "border-line-strong"}`}
      />
      {error && <p className="text-sm leading-5 font-semibold text-danger">{error}</p>}
    </div>
  );
}

/**
 * The edit/add form of one plan in a bottom sheet. State lives here, so it survives the sheet
 * closing for the fee Dialog and comes back if the person cancels that Dialog.
 * @param {{ plan: Plan | null, open: boolean, onOpenChange: (open: boolean) => void }} props
 */
function PlanSheet({ plan, open, onOpenChange }) {
  const router = useRouter();
  const isNew = plan === null;
  const [name, setName] = useState(plan?.name ?? "");
  const [code, setCode] = useState("");
  const [kind, setKind] = useState("autopilot");
  const [fee, setFee] = useState(plan?.fee_paise == null ? "" : String(plan.fee_paise / 100));
  const [live, setLive] = useState(plan?.state === "live");
  const [features, setFeatures] = useState(plan?.features.join("\n") ?? "");
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const [failure, setFailure] = useState("");
  const [confirm, setConfirm] = useState(false);
  const confirmed = useRef(false);
  const [saving, startSaving] = useTransition();
  /** One Idempotency-Key per save; dropped when a value changes. */
  const key = useRef(/** @type {string | null} */ (null));

  /** @template T @param {(v: T) => void} setter @returns {(v: T) => void} */
  const edit = (setter) => (v) => {
    key.current = null;
    setter(v);
  };

  const feePaise = fee.trim() === "" ? null : rupeesToPaise(fee);
  const feeChanged = feePaise !== (plan?.fee_paise ?? null);
  const feeText = feePaise === null ? "no fee" : `${formatPaise(feePaise)} a month`;

  function check() {
    /** @type {Record<string, string>} */
    const e = {};
    if (!name.trim()) e.name = "Give the plan a name";
    if (isNew && !/^[a-z][a-z0-9_]{1,23}$/.test(code))
      e.code = "Use 2 to 24 lower-case letters, digits or _";
    if (fee.trim() !== "" && feePaise === null) e.fee_paise = "Enter the fee in rupees, like 299";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  function onSave() {
    if (!check()) return;
    if (feeChanged && !(isNew && feePaise === null)) {
      confirmed.current = false;
      onOpenChange(false);
      setConfirm(true);
      return;
    }
    send();
  }

  function send() {
    key.current ??= crypto.randomUUID();
    const idempotencyKey = key.current;
    const input = {
      name: name.trim(),
      fee_paise: feePaise,
      state: /** @type {"live" | "draft"} */ (live ? "live" : "draft"),
      features: features
        .split("\n")
        .map((f) => f.trim())
        .filter(Boolean),
      ...(isNew ? { code, kind } : {}),
    };
    startSaving(async () => {
      const r = await savePlan(plan?.code ?? null, input, idempotencyKey).catch(() => ({
        ok: /** @type {const} */ (false),
        message: "That did not go through. Check your connection and try again.",
        fields: /** @type {Record<string, string>} */ ({}),
      }));
      if (!r.ok) {
        setErrors(r.fields);
        setFailure(r.message);
        onOpenChange(true);
        return;
      }
      key.current = null;
      toast(isNew ? `${r.plan.name} added` : `${r.plan.name} saved`);
      router.refresh();
    });
  }

  return (
    <>
      <Sheet
        title={isNew ? "Add a plan" : `Edit plan · ${plan.name}`}
        description={isNew ? "A new plan starts as a draft until you make it live." : plan.code}
        open={open}
        onOpenChange={onOpenChange}
        footer={
          <>
            <Button variant="quiet" onPress={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="primary" isDisabled={saving} onPress={onSave}>
              {isNew ? "Add plan" : "Save plan"}
            </Button>
          </>
        }
      >
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
          value={name}
          onChange={edit(setName)}
          isInvalid={!!errors.name}
          errorMessage={errors.name}
        />
        {isNew && (
          <>
            <TextField
              label="Code"
              description="Used in the app and the API. It cannot change later."
              value={code}
              onChange={edit(setCode)}
              isInvalid={!!errors.code}
              errorMessage={errors.code}
            />
            <Select label="Kind" value={kind} onChange={edit(setKind)} options={KIND_OPTIONS} />
          </>
        )}
        <TextField
          label="Monthly fee"
          prefix="₹"
          inputMode="decimal"
          description="Leave empty until pricing is decided."
          value={fee}
          onChange={edit(setFee)}
          isInvalid={!!errors.fee_paise}
          errorMessage={errors.fee_paise}
        />
        <TextArea
          id={`features-${plan?.code ?? "new"}`}
          label="Features"
          description="One per line, shown on the plan card."
          value={features}
          onChange={edit(setFeatures)}
          error={errors.features}
        />
        <Switch
          label="Live"
          description="Households can subscribe to a live plan; a draft stays hidden."
          isSelected={live}
          onChange={edit(setLive)}
        />
      </Sheet>
      <Dialog
        title={feePaise === null ? "Remove the monthly fee?" : `Set the fee to ${feeText}?`}
        confirmLabel={feePaise === null ? "Remove the fee" : `Set ${feeText}`}
        cancelLabel="Keep editing"
        isOpen={confirm}
        onOpenChange={(o) => {
          setConfirm(o);
          if (!o && !confirmed.current) onOpenChange(true);
        }}
        onConfirm={() => {
          confirmed.current = true;
          send();
        }}
      >
        Households on {name.trim() || plan?.name} will be billed this amount each month.
      </Dialog>
    </>
  );
}
