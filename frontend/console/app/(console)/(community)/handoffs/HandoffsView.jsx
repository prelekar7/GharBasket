"use client";

import { Button, Select, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PageHeader, Panel } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { downloadCsv } from "@/lib/csv.js";
import { monthLabel } from "@/lib/months.js";
import { claimOffer, confirmHandoff, presignPhoto, recordHandoff } from "./actions.js";

/**
 * M9 section 3.5: hand-offs, what is ready to hand over, the NGOs and the surplus on offer.
 * @typedef {{ id: string, ngo: { id: string, name: string }, kind: "items" | "food" | "meals",
 *   summary: { items: number, kg: number, meals: number }, photos: number, people_reached: number | null,
 *   handed_at: string, recorded_by: "hub" | "ngo", confirmed_at: string | null, note: string | null }} Handoff
 * @typedef {{ handoffs: number, items: number, kg: number, meals: number, people_reached: number }} Report
 * @typedef {{ offer_id: string, product: { name: string }, qty: number, unit: string, kg: number,
 *   expires_at: string, status: string }} Offer
 * @typedef {{ donations: { id: string, items: { code: string, unit: string, counted: number | null }[], verified_on: string }[],
 *   offers: Offer[], meals_waiting: number }} Ready
 * @typedef {{ id: string, name: string, verified: boolean, status: string, accepts: string[] }} Ngo
 * @typedef {import("@/lib/types.js").Column} Column
 */

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
  timeZone: "Asia/Kolkata",
});
/** "Thu 1 Oct". @param {string} iso */
const day = (iso) => dayFmt.format(new Date(iso)).replace(",", "").replace("Sept", "Sep");

/** "4 items, 3 kg", "1.5 kg of surplus food", "6 meals". @param {Handoff["summary"]} s @param {Handoff["kind"]} kind */
function summaryText(s, kind) {
  if (kind === "meals") return `${s.meals} meals`;
  if (kind === "food") return `${s.kg} kg of surplus food`;
  return `${s.items} ${s.items === 1 ? "item" : "items"}${s.kg > 0 ? `, ${s.kg} kg` : ""}`;
}

/** The hand-off's state for the Proof column. @param {Handoff} h */
const proofBadge = (h) =>
  h.confirmed_at
    ? { tone: /** @type {const} */ ("primary"), label: "Confirmed" }
    : { tone: /** @type {const} */ ("accent"), label: "Awaiting NGO" };

/**
 * @param {{ handoffs: Handoff[], report: Report, ready: Ready, ngos: Ngo[], offers: Offer[],
 *   months: { value: string, label: string }[], month: string,
 *   role: string | null, hubId: string }} props
 */
export function HandoffsView({
  handoffs,
  report,
  ready,
  ngos,
  offers,
  months,
  month,
  role,
  hubId,
}) {
  const [, setMonth] = useQueryState("month", parseAsString.withOptions({ shallow: false }));
  const label = monthLabel(month);
  const name = label.split(" ")[0];
  const isNgo = role === "ngo_coordinator";
  const awaiting = handoffs.filter((h) => !h.confirmed_at && isNgo);

  function download() {
    downloadCsv(`gharbasket-handoffs-${month}.csv`, [
      ["Date", "Kind", "Items", "Kg", "Meals", "Given to", "People reached", "Photos", "Confirmed"],
      ...handoffs.map((h) => [
        h.handed_at.slice(0, 10),
        h.kind,
        h.summary.items,
        h.summary.kg,
        h.summary.meals,
        h.ngo.name,
        h.people_reached ?? "",
        h.photos,
        h.confirmed_at ? "yes" : "awaiting",
      ]),
    ]);
    toast(`${name} hand-offs downloaded`);
  }

  return (
    <>
      <PageHeader
        title="Hand-offs"
        aside={
          <div data-no-print className="w-[200px]">
            <Select label="Month" options={months} value={month} onChange={setMonth} />
          </div>
        }
      >
        Record what you received from the hub and where it went, with a photo. Each hand-off is
        proof for the families who gave, and for your own reports.
      </PageHeader>
      <Toasts />
      <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <DataTable
            caption={`Hand-offs in ${label}`}
            columns={COLUMNS}
            rows={handoffs.map((h) => ({
              id: h.id,
              when: day(h.handed_at),
              items: { text: summaryText(h.summary, h.kind), sub: h.note ?? undefined },
              to: h.ngo.name,
              reach: h.people_reached ?? "—",
              proof: { badge: proofBadge(h) },
            }))}
            footer="Photos are stored privately; faces of the people you help are never required."
            labels={{ empty: "No hand-offs recorded this month." }}
          />
          <Panel
            title="Monthly report"
            aside={
              <div data-no-print className="flex flex-wrap gap-2">
                <Button variant="primary" onPress={() => window.print()}>
                  {`Print ${name} report`}
                </Button>
                <Button onPress={download}>{`Download ${name} (CSV)`}</Button>
              </div>
            }
          >
            <p className="text-sm leading-5 text-fg-secondary">
              Items received, hand-offs, people reached, and the families’ societies (never their
              names). {name}: {report.handoffs} {report.handoffs === 1 ? "hand-off" : "hand-offs"},{" "}
              {report.items} items, {report.kg} kg, {report.meals} meals, {report.people_reached}{" "}
              people reached.
            </p>
          </Panel>
        </div>
        <div data-no-print className="flex min-w-0 flex-col gap-4">
          {awaiting.length > 0 && <ConfirmPanel handoffs={awaiting} />}
          <OffersPanel offers={offers} ngoId={isNgo ? ngos[0]?.id : undefined} />
          <HandoffForm
            ready={ready}
            ngos={ngos}
            hubId={hubId}
            ownNgo={isNgo ? ngos[0]?.id : undefined}
          />
        </div>
      </div>
    </>
  );
}

/** Hand-offs the hub recorded that this NGO has not confirmed yet. @param {{ handoffs: Handoff[] }} props */
function ConfirmPanel({ handoffs }) {
  const [pending, start] = useTransition();
  const attempt = useRef(crypto.randomUUID());
  return (
    <Panel title="Confirm receipt" label="Confirm receipt">
      {handoffs.map((h) => (
        <div key={h.id} className="flex items-center justify-between gap-3">
          <span className="text-sm leading-5">
            {day(h.handed_at)} · {summaryText(h.summary, h.kind)}
          </span>
          <Button
            isDisabled={pending}
            onPress={() =>
              start(async () => {
                const res = await confirmHandoff(h.id, attempt.current);
                attempt.current = crypto.randomUUID();
                toast(res.ok ? "Receipt confirmed" : res.message);
              })
            }
          >
            Confirm
          </Button>
        </div>
      ))}
    </Panel>
  );
}

/** Surplus the hub offers to NGOs; the first claim wins. @param {{ offers: Offer[], ngoId?: string }} props */
function OffersPanel({ offers, ngoId }) {
  const [pending, start] = useTransition();
  const attempt = useRef(crypto.randomUUID());
  if (offers.length === 0) return null;
  return (
    <Panel title="Surplus on offer" label="Surplus on offer">
      {offers.map((o) => (
        <div key={o.offer_id} className="flex items-center justify-between gap-3">
          <span className="text-sm leading-5">
            <strong>{o.product.name}</strong> · {o.qty} {o.unit}
          </span>
          {ngoId && (
            <Button
              isDisabled={pending}
              onPress={() =>
                start(async () => {
                  const res = await claimOffer(o.offer_id, ngoId, attempt.current);
                  attempt.current = crypto.randomUUID();
                  toast(res.ok ? `${o.product.name} is yours to collect` : res.message);
                })
              }
            >
              Claim
            </Button>
          )}
        </div>
      ))}
    </Panel>
  );
}

/**
 * What can be handed over: all verified donations as one gift of items, each claimed offer as food, and the
 * donated meals waiting.
 * @param {Ready} ready
 */
function choices(ready) {
  const pieces = ready.donations.flatMap((d) => d.items).filter((i) => i.unit === "each");
  const count = pieces.reduce((n, i) => n + (i.counted ?? 0), 0);
  return [
    ...(ready.donations.length > 0
      ? [
          {
            value: "items",
            label: `Items · ${ready.donations.length} verified ${ready.donations.length === 1 ? "donation" : "donations"}, ${count} pieces`,
          },
        ]
      : []),
    ...ready.offers.map((o) => ({
      value: `food:${o.offer_id}`,
      label: `Food · ${o.product.name} ${o.qty} ${o.unit}`,
    })),
    ...(ready.meals_waiting > 0
      ? [{ value: "meals", label: `Meals · ${ready.meals_waiting} waiting` }]
      : []),
  ];
}

/**
 * @param {{ ready: Ready, ngos: Ngo[], hubId: string, ownNgo?: string }} props
 */
function HandoffForm({ ready, ngos, hubId, ownNgo }) {
  const [pending, start] = useTransition();
  const attempt = useRef(crypto.randomUUID());
  const options = choices(ready);
  const [what, setWhat] = useState(options[0]?.value ?? "");
  const [ngo, setNgo] = useState(ownNgo ?? "");
  const [note, setNote] = useState("");
  const [people, setPeople] = useState("");
  const [photos, setPhotos] = useState(/** @type {File[]} */ ([]));
  const [errors, setErrors] = useState(/** @type {Record<string, string>} */ ({}));
  const current = options.some((o) => o.value === what) ? what : (options[0]?.value ?? "");
  const kind = current.startsWith("food:") ? "food" : current;
  const takers = ngos.filter(
    (n) => n.verified && n.status === "active" && n.accepts.includes(kind),
  );
  const chosenNgo = takers.some((n) => n.id === ngo) ? ngo : (takers[0]?.id ?? "");

  /** Uploads each photo straight to storage and returns the keys, or says why not. @returns {Promise<string[] | null>} */
  async function upload() {
    /** @type {string[]} */
    const keys = [];
    for (const file of photos) {
      const signed = await presignPhoto(file.type, file.size);
      if (!signed.ok) {
        toast(signed.message);
        return null;
      }
      const put = await fetch(signed.url, { method: "PUT", headers: signed.headers, body: file });
      if (!put.ok) {
        toast("The photo didn’t upload. Try again.");
        return null;
      }
      keys.push(signed.key);
    }
    return keys;
  }

  function save() {
    setErrors({});
    if (photos.length === 0) {
      setErrors({ photo: "Add a photo of the items being handed over" });
      return;
    }
    const reached = people.trim() === "" ? null : Number(people);
    if (reached !== null && !(Number.isInteger(reached) && reached >= 0)) {
      setErrors({ people_reached: "Enter a whole number" });
      return;
    }
    start(async () => {
      const keys = await upload();
      if (!keys) return;
      const res = await recordHandoff(
        {
          ngo_id: chosenNgo,
          hub_id: hubId,
          kind: /** @type {"items" | "food" | "meals"} */ (kind),
          donation_ids: kind === "items" ? ready.donations.map((d) => d.id) : [],
          offer_ids: kind === "food" ? [current.slice("food:".length)] : [],
          meals: kind === "meals" ? ready.meals_waiting : 0,
          photo_keys: keys,
          people_reached: reached,
          note: note.trim() || null,
        },
        attempt.current,
      );
      if (!res.ok) {
        setErrors(res.fields);
        if (Object.keys(res.fields).length === 0) toast(res.message);
        return;
      }
      attempt.current = crypto.randomUUID();
      setNote("");
      setPeople("");
      setPhotos([]);
      toast("Hand-off saved");
    });
  }

  return (
    <Panel title="Record a hand-off" label="Record a hand-off">
      {options.length === 0 ? (
        <p className="text-sm leading-5 text-fg-secondary">
          Nothing is waiting to be handed over. When the hub verifies donations, offers you claim
          surplus or customers give meals, they appear here.
        </p>
      ) : (
        <>
          <Select label="What you received" options={options} value={current} onChange={setWhat} />
          {!ownNgo && (
            <Select
              label="Given to"
              options={takers.map((n) => ({ value: n.id, label: n.name }))}
              value={chosenNgo}
              onChange={setNgo}
              error={takers.length === 0 ? "No verified NGO takes this yet" : errors.ngo_id}
            />
          )}
          <TextField
            label="Note"
            placeholder="e.g. Class 5, municipal school, Sector 12"
            value={note}
            onChange={setNote}
          />
          <TextField
            label="People reached"
            inputMode="numeric"
            placeholder="Number of people, if you know"
            value={people}
            onChange={setPeople}
            isInvalid={!!errors.people_reached}
            errorMessage={errors.people_reached}
          />
          <label className="flex min-h-24 cursor-pointer flex-col items-center justify-center gap-1 rounded-sm border-[1.5px] border-dashed border-line-strong bg-bg p-3 text-center text-sm text-fg-secondary focus-within:shadow-focus">
            <span className="font-semibold text-fg">
              {photos.length > 0
                ? photos.map((p) => p.name).join(", ")
                : "Add up to 3 photos of the items being handed over"}
            </span>
            {photos.length === 0 && <span>Tap to take or choose a photo</span>}
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              className="sr-only"
              onChange={(e) => setPhotos(Array.from(e.target.files ?? []).slice(0, 3))}
            />
          </label>
          {(errors.photo || errors.photo_keys) && (
            <p role="alert" className="text-sm leading-5 font-semibold text-danger">
              {errors.photo ?? errors.photo_keys}
            </p>
          )}
          <Button variant="primary" block isDisabled={pending || !chosenNgo} onPress={save}>
            Save hand-off
          </Button>
        </>
      )}
    </Panel>
  );
}
