"use client";

import { Button, Dialog, EmptyState, Select, Tabs, toast } from "@gharbasket/ui";
import { useRouter } from "next/navigation";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PageHead, TABLE, TD, TD_ACT, TH } from "@/components/admin/Page.jsx";
import { whenLabel } from "@/lib/admin/format.js";
import { addAsProspect, markDuplicate, reopenRequest } from "./actions.js";

/** @typedef {import("@/lib/admin/types.js").SocietyRequest} SocietyRequest */

const TABS = /** @type {const} */ (["received", "accepted", "duplicate"]);
const EMPTY = {
  received: [
    "No new requests",
    "When a customer asks for a society we don't list, it shows up here.",
  ],
  accepted: ["Nothing added yet", "Requests you add as prospects are listed here."],
  duplicate: ["No duplicates", "Requests you mark as a society we already have are listed here."],
};

/**
 * Society requests: customers asking us to come to a society we don't list. Phone numbers of the
 * requesters are not in this data at all, so they cannot be shown by mistake.
 * @param {{ requests: SocietyRequest[], societies: { id: string, name: string, area: string }[], now: number }} props
 */
export function RequestsView({ requests, societies, now }) {
  const router = useRouter();
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("received").withOptions({ history: "replace" }),
  );
  const [duplicateOf, setDuplicateOf] = useState(/** @type {SocietyRequest | null} */ (null));
  const [target, setTarget] = useState("");
  const [, run] = useTransition();
  const keys = useRef(/** @type {Map<string, string>} */ (new Map()));
  /** One idempotency key per request and kind of click, kept until it succeeds. @param {string} id */
  const keyFor = (id) => {
    const known = keys.current.get(id);
    if (known) return known;
    const fresh = crypto.randomUUID();
    keys.current.set(id, fresh);
    return fresh;
  };

  const byId = new Map(societies.map((s) => [s.id, s]));
  const rows = requests.filter((r) => r.status === tab);
  const fresh = requests.filter((r) => r.status === "received").length;

  /** @param {SocietyRequest} r */
  function add(r) {
    run(async () => {
      const result = await addAsProspect(keyFor(r.id), r.id);
      if (!result.ok) {
        toast(result.message);
        return;
      }
      keys.current.delete(r.id);
      toast(`${r.name} added as a prospect`, {
        action: {
          label: "Open",
          onClick: () => router.push(`/societies?society=${result.data.societyId}`),
        },
      });
    });
  }

  function confirmDuplicate() {
    const r = duplicateOf;
    if (!r || !target) return;
    run(async () => {
      const result = await markDuplicate(keyFor(`${r.id}:dup:${target}`), r.id, target);
      if (!result.ok) {
        toast(result.message);
        return;
      }
      keys.current.delete(`${r.id}:dup:${target}`);
      toast(`${r.name} marked as a duplicate`, {
        action: { label: "Undo", onClick: () => void reopenRequest(r.id) },
      });
    });
  }

  const options = societies.map((s) => ({ value: s.id, label: `${s.name} (${s.area})` }));

  return (
    <>
      <PageHead title="Society requests">
        Customers asking us to come to a society we don't list yet. Add it as a prospect, or mark it
        a duplicate of one we have.
      </PageHead>

      <Tabs
        label="Requests"
        tabs={[
          { value: "received", label: "New", count: fresh },
          { value: "accepted", label: "Added" },
          { value: "duplicate", label: "Duplicates" },
        ]}
        value={tab}
        onChange={(v) => void setTab(/** @type {typeof tab} */ (v))}
      >
        {rows.length === 0 ? (
          <EmptyState title={EMPTY[tab][0]} body={EMPTY[tab][1]} />
        ) : (
          <div className="overflow-x-auto rounded-md border border-line bg-bg">
            <table className={TABLE}>
              <caption className="sr-only">Society requests</caption>
              <thead>
                <tr>
                  <th className={TH}>Society asked for</th>
                  <th className={TH}>Area</th>
                  <th className={TH}>Pin</th>
                  <th className={TH}>Asked</th>
                  <th className={TH}>Note</th>
                  <th className={TH}>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className={`${TD} font-semibold`}>{r.name}</td>
                    <td className={TD}>{r.area}</td>
                    <td className={`${TD} text-fg-secondary`}>
                      {r.location ? "Placed on the map" : "Not placed"}
                    </td>
                    <td className={`${TD} whitespace-nowrap text-fg-secondary`}>
                      {whenLabel(r.created_at, now)}
                    </td>
                    <td className={`${TD} min-w-48`}>{r.note ?? "—"}</td>
                    <td className={TD_ACT}>
                      <div className="flex justify-end gap-1.5">
                        {r.status === "received" && (
                          <>
                            <Button
                              variant="quiet"
                              onPress={() => {
                                setTarget(societies[0]?.id ?? "");
                                setDuplicateOf(r);
                              }}
                            >
                              Duplicate
                            </Button>
                            <Button onPress={() => add(r)}>Add as prospect</Button>
                          </>
                        )}
                        {r.status === "accepted" && r.society_id && (
                          <Button
                            variant="quiet"
                            onPress={() => router.push(`/societies?society=${r.society_id}`)}
                          >
                            Open society
                          </Button>
                        )}
                        {r.status === "duplicate" && (
                          <span className="py-2 text-fg-secondary">
                            Duplicate of{" "}
                            {byId.get(r.duplicate_of ?? "")?.name ?? "a listed society"}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Tabs>

      <p className="text-sm leading-5 text-fg-secondary">
        Requesters' phone numbers stay hidden here. When their society goes live, they hear about it
        in the app.
      </p>

      <Dialog
        title={`Mark ${duplicateOf?.name ?? "this request"} a duplicate?`}
        confirmLabel="Mark duplicate"
        cancelLabel="Cancel"
        isOpen={duplicateOf !== null}
        onOpenChange={(open) => !open && setDuplicateOf(null)}
        onConfirm={confirmDuplicate}
      >
        <Select
          label="Which society is it?"
          options={options}
          value={target}
          onChange={setTarget}
        />
      </Dialog>
    </>
  );
}
