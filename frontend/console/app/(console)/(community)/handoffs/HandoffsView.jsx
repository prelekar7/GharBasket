"use client";

import { Button, Select, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useRef, useState, useTransition } from "react";
import { LinkButton } from "@/components/LinkButton.jsx";
import { PageHeader, Panel } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { downloadCsv } from "@/lib/csv.js";
import { recordHandoff } from "./actions.js";

/** @typedef {import("@/mocks/impact.js").Handoff} Handoff */
/** @typedef {import("@/mocks/impact.js").Batch} Batch */
/** @typedef {import("@/lib/types.js").Column} Column */

/** @type {Column[]} */
const COLUMNS = [
  { key: "when", label: "Date" },
  { key: "items", label: "Items" },
  { key: "to", label: "Given to" },
  { key: "reach", label: "People", align: "end" },
  { key: "proof", label: "Proof" },
];

const dayFmt = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});
/** "Thu 1 Oct". @param {string} iso */
const day = (iso) =>
  dayFmt
    .format(new Date(`${iso}T00:00:00Z`))
    .replace(",", "")
    .replace("Sept", "Sep");

/** @param {{ data: { month: string, label: string, items: Handoff[], open_batches: Batch[], report: { handoffs: number, people_reached: number } } }} props */
export function HandoffsView({ data }) {
  const { items, open_batches: batches, report, label } = data;
  const month = label.split(" ")[0];

  function download() {
    downloadCsv(`gharbasket-handoffs-${month.toLowerCase()}.csv`, [
      ["Date", "Items", "Given to", "People reached", "Photo"],
      ...items.map((h) => [
        h.on,
        h.items,
        h.given_to,
        h.people_reached,
        h.proof === "photo" ? "yes" : "due",
      ]),
    ]);
    toast(`${month} hand-offs downloaded`);
  }

  return (
    <>
      <PageHeader title="Hand-offs">
        Record what you received from the hub and where it went, with a photo. Each hand-off is
        proof for the families who gave, and for your own reports.
      </PageHeader>
      <Toasts />
      <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <DataTable
            caption="Hand-offs this month"
            columns={COLUMNS}
            rows={items.map((h) => ({
              id: h.id,
              when: day(h.on),
              items: { text: h.items, sub: h.note },
              to: h.given_to,
              reach: h.people_reached,
              proof:
                h.proof === "photo"
                  ? { badge: { tone: "primary", label: "Photo" } }
                  : { badge: { tone: "accent", label: "Photo due" } },
            }))}
            footer="Photos are stored privately; faces of the people you help are never required."
            labels={{ empty: "No hand-offs recorded this month." }}
          />
          <Panel
            title="Monthly report"
            aside={
              <div className="flex flex-wrap gap-2">
                <LinkButton
                  href={`/handoffs/report?month=${data.month}`}
                  variant="primary"
                  download
                  onClick={() => toast(`${month} report is downloading`)}
                >
                  {`Download ${month} (PDF)`}
                </LinkButton>
                <Button onPress={download}>{`Download ${month} (CSV)`}</Button>
              </div>
            }
          >
            <p className="text-sm leading-5 text-fg-secondary">
              Items received, hand-offs, people reached, and the families’ societies (never their
              names). {month} so far: {report.handoffs}{" "}
              {report.handoffs === 1 ? "hand-off" : "hand-offs"}, {report.people_reached} people
              reached.
            </p>
          </Panel>
        </div>
        <HandoffForm batches={batches} />
      </div>
    </>
  );
}

/** @param {{ batches: Batch[] }} props */
function HandoffForm({ batches }) {
  const [pending, start] = useTransition();
  const attempt = useRef(crypto.randomUUID());
  const [batch, setBatch] = useState(batches[0]?.id ?? "");
  const [to, setTo] = useState("");
  const [people, setPeople] = useState("");
  const [photo, setPhoto] = useState(/** @type {File | null} */ (null));
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const options = batches.map((b) => ({
    value: b.id,
    label: `Received ${day(b.received_on)} · ${b.items}`,
  }));
  const current = batches.some((b) => b.id === batch) ? batch : (batches[0]?.id ?? "");

  function save() {
    const form = new FormData();
    form.set("batch_id", current);
    form.set("given_to", to);
    form.set("people_reached", people);
    form.set("idempotency_key", attempt.current);
    if (photo) form.set("photo", photo);
    setErrors({});
    start(async () => {
      const res = await recordHandoff(form);
      if (!res.ok) {
        setErrors(res.fields);
        if (Object.keys(res.fields).length === 0) toast(res.message);
        return;
      }
      attempt.current = crypto.randomUUID();
      setTo("");
      setPeople("");
      setPhoto(null);
      toast(photo ? "Hand-off saved" : "Hand-off saved. Add its photo when you have it.");
    });
  }

  return (
    <Panel title="Record a hand-off" label="Record a hand-off">
      {batches.length === 0 ? (
        <p className="text-sm leading-5 text-fg-secondary">
          Nothing is waiting to be handed over. When the hub passes you verified donations, they
          appear here.
        </p>
      ) : (
        <>
          <Select
            label="What you received"
            options={options}
            value={current}
            onChange={setBatch}
            error={errors.batch_id}
          />
          <TextField
            label="Given to"
            placeholder="e.g. Class 5, municipal school, Sector 12"
            value={to}
            onChange={setTo}
            isInvalid={!!errors.given_to}
            errorMessage={errors.given_to}
          />
          <TextField
            label="People reached"
            inputMode="numeric"
            placeholder="Number of people"
            value={people}
            onChange={setPeople}
            isInvalid={!!errors.people_reached}
            errorMessage={errors.people_reached}
          />
          <label className="flex min-h-24 cursor-pointer flex-col items-center justify-center gap-1 rounded-sm border-[1.5px] border-dashed border-line-strong bg-bg p-3 text-center text-sm text-fg-secondary focus-within:shadow-focus">
            <span className="font-semibold text-fg">
              {photo ? photo.name : "Add a photo of the items being handed over"}
            </span>
            {!photo && <span>Tap to take or choose a photo</span>}
            <input
              type="file"
              accept="image/*"
              className="sr-only"
              onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
            />
          </label>
          <Button variant="primary" block isDisabled={pending} onPress={save}>
            Save hand-off
          </Button>
        </>
      )}
    </Panel>
  );
}
