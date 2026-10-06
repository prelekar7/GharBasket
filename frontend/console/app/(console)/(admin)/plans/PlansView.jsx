"use client";

import {
  Badge,
  Button,
  Checkbox,
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
import { FEATURE_LABELS, featureLabel, PLAN_KINDS } from "@/lib/plans.js";
import { rupeesToPaise } from "@/lib/priceList.js";
import { savePlan } from "./actions.js";

/** @typedef {import("@/lib/plans.js").Plan} Plan */

/**
 * Plans (M3): what a household can subscribe to. Cards for each plan; Edit plan and Add plan open
 * a bottom sheet. Changing a monthly fee is a money change, so it asks to confirm first. The edit
 * endpoints have not shipped (a @proposal), so without them (`editable` false) the plans are read-only.
 * @param {{ plans: Plan[], editable: boolean }} props
 */
export function PlansView({ plans, editable }) {
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
        {editable && (
          <Button
            onPress={() => show("new")}
            isDisabled={plans.length >= Object.keys(PLAN_KINDS).length}
          >
            <PlusIcon />
            Add plan
          </Button>
        )}
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
                {p.state && (
                  <Badge tone={p.state === "live" ? "primary" : "outline"}>
                    {p.state === "live" ? "Live" : "Draft"}
                  </Badge>
                )}
              </div>
              <span className="text-sm leading-5 text-fg-secondary">
                {p.code} · {PLAN_KINDS[p.code]}
              </span>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm leading-5 text-fg-secondary">Monthly fee</span>
                <strong>{p.fee_paise === 0 ? "Not set" : formatPaise(p.fee_paise)}</strong>
              </div>
              {p.subscriber_count !== undefined && (
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm leading-5 text-fg-secondary">Subscribers</span>
                  <strong>{p.subscriber_count ?? "—"}</strong>
                </div>
              )}
              <ul className="m-0 list-disc pl-[18px] text-sm leading-[22px]">
                {p.features.map((f) => (
                  <li key={f}>{featureLabel(f)}</li>
                ))}
              </ul>
              {editable && (
                <Button block onPress={() => show(p)}>
                  Edit plan
                </Button>
              )}
            </section>
          ))}
        </div>
      )}
      <p className="text-sm leading-5 text-fg-secondary">
        {editable
          ? "Fees stay “Not set” until the team decides pricing. A subscriber count of “—” means that plan’s subscriptions are not tracked yet."
          : "Editing plans is not available yet. It needs endpoints that have not shipped, so this page only shows the plans. A fee of “Not set” means pricing is not decided."}
      </p>

      {target && (
        <PlanSheet
          key={opened}
          plan={target === "new" ? null : target}
          freeCodes={Object.keys(PLAN_KINDS).filter((c) => !plans.some((p) => p.code === c))}
          open={open}
          onOpenChange={setOpen}
        />
      )}
    </>
  );
}

/**
 * The edit/add form of one plan in a bottom sheet. State lives here, so it survives the sheet
 * closing for the fee Dialog and comes back if the person cancels that Dialog.
 * @param {{ plan: Plan | null, freeCodes: string[], open: boolean, onOpenChange: (open: boolean) => void }} props
 * `freeCodes` are the plan codes no plan has yet (what a new plan can be)
 */
function PlanSheet({ plan, freeCodes, open, onOpenChange }) {
  const router = useRouter();
  const isNew = plan === null;
  const [name, setName] = useState(plan?.name ?? "");
  const [code, setCode] = useState(/** @type {string} */ (freeCodes[0] ?? ""));
  const [fee, setFee] = useState(!plan?.fee_paise ? "" : String(plan.fee_paise / 100));
  const [live, setLive] = useState(plan?.state === "live");
  const [features, setFeatures] = useState(plan?.features ?? []);
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

  const feePaise = fee.trim() === "" ? 0 : rupeesToPaise(fee);
  const feeChanged = feePaise !== (plan?.fee_paise ?? 0);
  const feeText = feePaise === 0 ? "no fee" : `${formatPaise(feePaise ?? 0)} a month`;

  function check() {
    /** @type {Record<string, string>} */
    const e = {};
    if (!name.trim()) e.name = "Give the plan a name";
    if (isNew && !code) e.code = "Pick a plan code";
    if (feePaise === null) e.fee_paise = "Enter the fee in rupees, like 299";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  function onSave() {
    if (!check()) return;
    if (feeChanged) {
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
      fee_paise: /** @type {number} */ (feePaise),
      state: /** @type {"live" | "draft"} */ (live ? "live" : "draft"),
      features,
      ...(isNew ? { code } : {}),
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
          <Select
            label="Code"
            description="Used in the app and the API. It cannot change later."
            value={code}
            onChange={edit(setCode)}
            options={freeCodes.map((c) => ({
              value: c,
              label: `${c} (${PLAN_KINDS[/** @type {"autopilot"} */ (c)]})`,
            }))}
            error={errors.code}
          />
        )}
        <TextField
          label="Monthly fee"
          prefix="₹"
          inputMode="decimal"
          description="Leave empty (₹0) until pricing is decided."
          value={fee}
          onChange={edit(setFee)}
          isInvalid={!!errors.fee_paise}
          errorMessage={errors.fee_paise}
        />
        <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
          <legend className="mb-1 text-sm leading-5 font-semibold">Features</legend>
          {[...new Set([...Object.keys(FEATURE_LABELS), ...features])].map((f) => (
            <Checkbox
              key={f}
              label={featureLabel(f)}
              isSelected={features.includes(f)}
              onChange={(on) =>
                edit(setFeatures)(on ? [...features, f] : features.filter((x) => x !== f))
              }
            />
          ))}
          {errors.features && (
            <p className="text-sm leading-5 font-semibold text-danger">{errors.features}</p>
          )}
        </fieldset>
        <Switch
          label="Live"
          description="Households can subscribe to a live plan; a draft stays hidden."
          isSelected={live}
          onChange={edit(setLive)}
        />
      </Sheet>
      <Dialog
        title={feePaise === 0 ? "Remove the monthly fee?" : `Set the fee to ${feeText}?`}
        confirmLabel={feePaise === 0 ? "Remove the fee" : `Set ${feeText}`}
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
