"use client";

import { Badge, Button, EmptyState, Sheet, TextField, toast } from "@gharbasket/ui";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { formatDay, plural } from "@/lib/format.js";
import { withUnit } from "@/lib/ops-units.js";
import { markLost, recordReturnedBag } from "./actions.js";

/**
 * GET /v1/hubs/{id}/kept-items (M6 §3.5, built), already ordered by society, then flat.
 * @typedef {{ id: string, society: { id: string, name: string }, flat_label: string | null, name: string,
 *   step: { label: string }, qty: number, kept_at: string }} KeptItem
 * @typedef {{ order_id: string, society: { name: string }, flat_label: string | null, kept: KeptItem[],
 *   lots: { id: string, name: string, unit: string, qty_in: number, grade: string }[] }} ReturnedBag
 */

/** @param {KeptItem[]} items @returns {{ society: string, flats: { flat: string, items: KeptItem[] }[] }[]} */
function group(items) {
  /** @type {Map<string, Map<string, KeptItem[]>>} */
  const societies = new Map();
  for (const i of items) {
    const flats = societies.get(i.society.name) ?? new Map();
    flats.set(i.flat_label ?? "Flat not known", [
      ...(flats.get(i.flat_label ?? "Flat not known") ?? []),
      i,
    ]);
    societies.set(i.society.name, flats);
  }
  return [...societies].map(([society, flats]) => ({
    society,
    flats: [...flats].map(([flat, rows]) => ({ flat, items: rows })),
  }));
}

/** @param {{ items: KeptItem[] }} props */
export function KeptItemsView({ items }) {
  const router = useRouter();
  const [lost, setLost] = useState(/** @type {KeptItem | null} */ (null));
  const [note, setNote] = useState("");
  const [bagOpen, setBagOpen] = useState(false);
  const [bag, setBag] = useState("");
  const [bagResult, setBagResult] = useState(/** @type {ReturnedBag | null} */ (null));
  const [error, setError] = useState("");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [busy, startTransition] = useTransition();

  /** @param {KeptItem | null} item */
  function openLost(item) {
    setLost(item);
    setNote("");
    setError("");
    setKey(crypto.randomUUID());
  }
  function openBag(open = true) {
    setBagOpen(open);
    setBag("");
    setBagResult(null);
    setError("");
    setKey(crypto.randomUUID());
  }

  function submitLost() {
    if (!lost) return;
    if (!note.trim()) return setError("Say what happened, for the audit log");
    startTransition(async () => {
      const res = await markLost(lost.id, note.trim(), key);
      if (!res.ok) {
        setError(res.fields.note ?? res.message);
        if (res.message.includes("Reload")) router.refresh();
        return;
      }
      toast(`${lost.name} marked lost. The household is refunded.`);
      openLost(null);
      router.refresh();
    });
  }

  function submitBag() {
    const code = bag.trim();
    if (!/^GB1:\S{1,56}$/.test(code))
      return setError("Scan or type the code on the bag label (it starts with GB1:)");
    startTransition(async () => {
      const res = await recordReturnedBag(code, key);
      if (!res.ok) return setError(res.fields.bag_qr ?? res.message);
      setError("");
      setBagResult(res.data);
      router.refresh();
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="m-0 font-display text-[28px] leading-[34px] font-semibold">Kept items</h1>
          <p className="m-0 max-w-[70ch] text-sm text-fg-secondary">
            After a second missed delivery a household’s non-perishable items wait on the shelf and
            go into its next box. If one can’t be found, mark it lost and the household is refunded.
          </p>
        </div>
        <Button variant="primary" onPress={() => openBag()}>
          Record a returned bag
        </Button>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="Nothing is being kept"
          body="Items appear here when a bag comes back after a second missed delivery."
        />
      ) : (
        group(items).map(({ society, flats }) => (
          <section key={society} className="flex min-w-0 flex-col gap-1 rounded-md bg-surface p-4">
            <h2 className="m-0 text-[17px] leading-6 font-bold">{society}</h2>
            {flats.map(({ flat, items: rows }) => (
              <div key={flat} className="flex flex-col border-b border-line py-2 last:border-b-0">
                <strong className="text-sm">{flat}</strong>
                {rows.map((i) => (
                  <div
                    key={i.id}
                    className="flex flex-wrap items-center justify-between gap-3 py-1.5"
                  >
                    <span className="flex flex-col">
                      <span>
                        {i.name} · {i.step.label}
                        {i.qty > 1 && ` × ${i.qty}`}
                      </span>
                      <span className="text-sm text-fg-secondary">
                        On the shelf since {formatDay(i.kept_at)}
                      </span>
                    </span>
                    <Button isDisabled={busy} onPress={() => openLost(i)}>
                      Mark lost
                    </Button>
                  </div>
                ))}
              </div>
            ))}
          </section>
        ))
      )}

      <Sheet
        title={lost ? `Mark ${lost.name} lost?` : "Mark lost"}
        description={
          lost
            ? `${lost.society.name}, ${lost.flat_label ?? "flat not known"}. The household is refunded this item at the price it paid, with no fee, and the item leaves the shelf. This can’t be undone.`
            : undefined
        }
        open={lost !== null}
        onOpenChange={(o) => !o && openLost(null)}
        footer={
          <>
            <Button onPress={() => openLost(null)}>Not yet</Button>
            <Button variant="danger" isDisabled={busy} onPress={submitLost}>
              Mark lost and refund
            </Button>
          </>
        }
      >
        <TextField
          label="What happened"
          description="Kept in the audit log, up to 500 characters"
          value={note}
          onChange={(v) => {
            setNote(v);
            setError("");
            setKey(crypto.randomUUID()); // another note is another request
          }}
          isInvalid={!!error}
          errorMessage={error}
        />
      </Sheet>

      <Sheet
        title="Record a returned bag"
        description="Scan the label of a bag that came back after a second miss. Its household items stay on the shelf; the rest becomes grade C stock for the 12:30 run."
        open={bagOpen}
        onOpenChange={(o) => !o && openBag(false)}
        footer={
          bagResult ? (
            <Button variant="primary" onPress={() => openBag(false)}>
              Done
            </Button>
          ) : (
            <>
              <Button onPress={() => openBag(false)}>Cancel</Button>
              <Button variant="primary" isDisabled={busy} onPress={submitBag}>
                Record bag
              </Button>
            </>
          )
        }
      >
        {bagResult ? (
          <div className="flex flex-col gap-2">
            <strong>
              {bagResult.society.name}, {bagResult.flat_label ?? "flat not known"}
            </strong>
            <p className="m-0 text-sm text-fg-secondary">
              {plural(bagResult.kept.length, "kept item")} on the shelf:{" "}
              {bagResult.kept.map((k) => `${k.name} (${k.step.label})`).join(", ") || "none"}.
            </p>
            {bagResult.lots.map((l) => (
              <span key={l.id} className="flex items-center gap-2 text-sm">
                <Badge tone="neutral">Grade {l.grade}</Badge>
                {l.name} · {withUnit(l.qty_in, l.unit)}, offered at 12:30
              </span>
            ))}
          </div>
        ) : (
          <TextField
            label="Bag label code"
            description="Starts with GB1:. A scanner types it for you."
            autoFocus
            value={bag}
            onChange={(v) => {
              setBag(v);
              setError("");
              setKey(crypto.randomUUID()); // another bag is another request
            }}
            isInvalid={!!error}
            errorMessage={error}
          />
        )}
      </Sheet>
    </div>
  );
}
