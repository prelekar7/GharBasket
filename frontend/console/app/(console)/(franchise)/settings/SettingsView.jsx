"use client";

import { Checkbox, Dialog, Select, Switch, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useState, useTransition } from "react";
import { PageHeader } from "@/components/franchise/PageHeader.jsx";
import { dayAndTime } from "@/lib/dates.js";
import { updateSettings } from "./actions.js";

/** @typedef {import("./data.js").Editable} Editable */
/** @typedef {import("./data.js").Switches} Switches */

/** @type {{ key: keyof Switches, label: string, description: string }[]} */
const SWITCHES = [
  {
    key: "ai_planner",
    label: "AI meal planner",
    description: "Off sends every family the rule-based box. Use only if the planner misbehaves.",
  },
  {
    key: "auto_approve",
    label: "Auto-approve for families who chose it",
    description: "At the cut-off, their unapproved week is approved for them.",
  },
  { key: "tiffins", label: "Tiffins", description: "Off hides tiffin booking for this hub." },
  {
    key: "upi_autopay",
    label: "UPI Autopay (pilot)",
    description: "Families can set a mandate instead of keeping a wallet balance.",
  },
];

const LANGUAGES = /** @type {const} */ ([
  { value: "en", label: "English" },
  { value: "hi", label: "हिन्दी" },
  { value: "mr", label: "मराठी" },
]);

const QUIET = [
  { value: "22-07", label: "10 PM to 7 AM" },
  { value: "21-08", label: "9 PM to 8 AM" },
  { value: "23-06", label: "11 PM to 6 AM" },
  { value: "off", label: "No quiet hours" },
];

/**
 * Hub settings. The delivery-day clock is read-only here; the switches, languages and quiet hours apply
 * at once, with an undo toast. UPI Autopay changes how families pay, so it asks first.
 * @param {{ settings: import("./data.js").HubSettings }} props
 */
export function SettingsView({ settings }) {
  const [value, setValue] = useState(
    /** @type {Editable} */ ({
      switches: settings.switches,
      languages: settings.languages,
      quiet_hours: settings.quiet_hours,
    }),
  );
  const [confirmAutopay, setConfirmAutopay] = useState(/** @type {boolean | null} */ (null));
  const [, startSaving] = useTransition();

  /**
   * Shows the change straight away, saves it, and puts it back if the save fails.
   * @param {Partial<Editable>} patch
   * @param {Partial<Editable> | null} undo what to send to undo it (null: this is the undo)
   * @param {string} message
   */
  function apply(patch, undo, message) {
    const before = value;
    setValue((v) => ({ ...v, ...patch, switches: { ...v.switches, ...patch.switches } }));
    startSaving(async () => {
      const result = await updateSettings(patch);
      if (!result.ok) {
        setValue(before);
        toast(result.message);
        return;
      }
      const { switches, languages, quiet_hours } = result.settings;
      setValue({ switches, languages, quiet_hours });
      toast(
        message,
        undo
          ? { action: { label: "Undo", onClick: () => apply(undo, null, "Change undone.") } }
          : {},
      );
    });
  }

  /** @param {keyof Switches} key @param {boolean} on */
  function flip(key, on) {
    const label = SWITCHES.find((s) => s.key === key)?.label ?? key;
    apply(
      { switches: /** @type {Switches} */ ({ [key]: on }) },
      { switches: /** @type {Switches} */ ({ [key]: !on }) },
      `${label} switched ${on ? "on" : "off"}.`,
    );
  }

  /** @param {import("./data.js").Language} lang @param {boolean} on */
  function setLanguage(lang, on) {
    const next = LANGUAGES.map((l) => l.value).filter((l) =>
      l === lang ? on : value.languages.includes(l),
    );
    apply({ languages: next }, { languages: value.languages }, "Languages saved.");
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title={`Settings · ${settings.hub_name}`}>
        The week’s clock and the switches that change what families see. Admin only; every change is
        logged with who made it and when.
      </PageHeader>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section
          aria-label="The delivery-day clock"
          className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
        >
          <h2 className="text-[17px] leading-6 font-bold">The delivery-day clock</h2>
          <DataTable
            dense
            caption="Scheduled jobs around a delivery day (T)"
            columns={[
              { key: "what", label: "What happens" },
              { key: "when", label: "When" },
              { key: "owner", label: "Module" },
            ]}
            rows={settings.clock.map((c) => ({
              id: c.key,
              what: { text: c.what, strong: true },
              when: c.when,
              owner: c.module,
            }))}
          />
          <p className="text-sm leading-5 text-fg-secondary">
            Changing a time applies from the next delivery week, never to a week already sent to
            families.
          </p>
        </section>

        <div className="flex min-w-0 flex-col gap-4">
          <section
            aria-label="Switches"
            className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
          >
            <h2 className="text-[17px] leading-6 font-bold">Switches</h2>
            {SWITCHES.map((s) => (
              <Switch
                key={s.key}
                label={s.label}
                description={s.description}
                isSelected={value.switches[s.key]}
                onChange={(on) =>
                  s.key === "upi_autopay" ? setConfirmAutopay(on) : flip(s.key, on)
                }
              />
            ))}
          </section>

          <section
            aria-label="Languages and notifications"
            className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
          >
            <h2 className="text-[17px] leading-6 font-bold">Languages and messages</h2>
            {LANGUAGES.map((l) => (
              <Checkbox
                key={l.value}
                label={l.label}
                isSelected={value.languages.includes(l.value)}
                isDisabled={value.languages.length === 1 && value.languages.includes(l.value)}
                onChange={(on) => setLanguage(l.value, on)}
              />
            ))}
            <Select
              label="Quiet hours for push messages"
              description="Payment and delivery alerts still go through."
              options={QUIET}
              value={value.quiet_hours}
              onChange={(v) =>
                apply(
                  { quiet_hours: /** @type {import("./data.js").QuietHours} */ (v) },
                  { quiet_hours: value.quiet_hours },
                  "Quiet hours saved.",
                )
              }
            />
          </section>
        </div>
      </div>

      <section
        aria-label="Change log"
        className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
      >
        <h2 className="text-[17px] leading-6 font-bold">Recent changes</h2>
        <DataTable
          dense
          caption="Recent changes to this hub's settings"
          columns={[
            { key: "at", label: "When" },
            { key: "by", label: "Who" },
            { key: "change", label: "What changed" },
          ]}
          rows={settings.changes.map((c, i) => ({
            id: String(i),
            at: dayAndTime(c.at),
            by: c.by,
            change: c.change,
          }))}
          labels={{ empty: "No changes yet." }}
        />
      </section>

      <Dialog
        title={confirmAutopay ? "Turn on UPI Autopay?" : "Turn off UPI Autopay?"}
        confirmLabel={confirmAutopay ? "Turn on" : "Turn off"}
        cancelLabel="Keep as it is"
        isOpen={confirmAutopay !== null}
        onOpenChange={(open) => !open && setConfirmAutopay(null)}
        onConfirm={() => confirmAutopay !== null && flip("upi_autopay", confirmAutopay)}
      >
        {confirmAutopay
          ? "Families in this hub can set up a mandate and be charged at the cut-off instead of keeping a wallet balance."
          : "Families stop being charged by mandate from the next delivery week and pay from their wallet again."}
      </Dialog>
    </div>
  );
}
