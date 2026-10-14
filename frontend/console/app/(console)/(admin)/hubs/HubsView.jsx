"use client";

import { Badge, Button, EmptyState, Sheet, TextField, toast } from "@gharbasket/ui";
import { DataTable, PolygonEditor } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { KV, PageHead, Panel } from "@/components/admin/Page.jsx";
import { polygonOf, ringOf } from "@/lib/admin/geo.js";
import "@/lib/maps.js";
import { createHub, saveServiceArea } from "./actions.js";

/** @typedef {import("@/lib/admin/types.js").AdminHub} AdminHub */
/** @typedef {import("@/lib/admin/types.js").Society} Society */

const TONE = /** @type {const} */ ({
  live: "primary",
  unlocking: "accent",
  prospect: "outline",
  paused: "neutral",
});
const LABEL = { live: "Live", unlocking: "Unlocking", prospect: "Prospect", paused: "Paused" };
const COLUMNS = [
  { key: "s", label: "Society" },
  { key: "st", label: "Status" },
];

/** "A", "A and B", "A, B and C". @param {string[]} names */
const listOf = (names) =>
  names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;

/**
 * Hubs and their service areas: pick a hub, see its facts, draw or move its service area on the
 * map, and see the societies inside it.
 * The admin list of hubs and of societies are @proposals (not in the contract yet). Without the hub list
 * (`limited`) only each hub's name is known, so the page is read-only; without the society list
 * (`societies` null) it cannot say which societies a hub serves.
 * @param {{ hubs: AdminHub[], societies: Society[] | null, currentId: string, limited: boolean }} props
 */
export function HubsView({ hubs, societies, currentId, limited }) {
  const [param, setParam] = useQueryState("hub", parseAsString.withOptions({ history: "replace" }));
  const [adding, setAdding] = useState(false);
  const hub = hubs.find((h) => h.id === param) ?? hubs.find((h) => h.id === currentId) ?? hubs[0];
  const [tool, setTool] = useState(/** @type {"draw" | "edit"} */ ("edit"));

  const addButton = limited ? null : <Button onPress={() => setAdding(true)}>Add hub</Button>;
  const head = (
    <>
      <PageHead title="Hubs" action={addButton}>
        Each hub is a franchise fulfilment centre with a service area. A society inside the area can
        be served once it unlocks.
      </PageHead>
      {limited && (
        <p
          role="status"
          className="rounded-md border border-line bg-surface px-3.5 py-3 text-sm leading-5"
        >
          Hub details, service areas and adding hubs are not available yet. They need endpoints that
          have not shipped, so this shows the hubs you can use, by name.
        </p>
      )}
    </>
  );

  if (!hub) {
    return (
      <>
        {head}
        <EmptyState title="No hubs yet" body="Add the first hub, then draw its service area." />
        <AddHub open={adding} onOpenChange={setAdding} onCreated={(id) => void setParam(id)} />
      </>
    );
  }

  const areas = hubs
    .filter((h) => h.service_area)
    .map((h) => ({
      id: h.id,
      label: `${h.name} · service area`,
      status: /** @type {"live" | "prospect"} */ (h.status === "active" ? "live" : "prospect"),
      coordinates: ringOf(h.service_area),
    }));
  const mine = (societies ?? []).filter((s) => s.hub_id === hub.id);
  const outside = (societies ?? []).filter((s) => s.hub_id === null);
  const rows = mine.map((s) => ({
    id: s.id,
    s: { text: s.name, sub: s.area, strong: true },
    st: { badge: { tone: TONE[s.status], label: LABEL[s.status] } },
  }));

  /** @param {import("@/lib/admin/geo.js").LngLat[] | null} ring */
  async function save(ring) {
    const before = hub.service_area;
    const result = await saveServiceArea(hub.id, ring ? polygonOf(ring) : null);
    if (!result.ok) {
      toast(result.message);
      return;
    }
    toast(ring ? "Service area saved" : "Service area deleted", {
      action: { label: "Undo", onClick: () => void saveServiceArea(hub.id, before) },
    });
  }

  return (
    <>
      {head}
      {hubs.length > 1 && (
        <fieldset className="m-0 flex min-w-0 flex-wrap gap-2 border-0 p-0">
          <legend className="sr-only">Hubs</legend>
          {hubs.map((h) => (
            <Button
              key={h.id}
              variant={h.id === hub.id ? "primary" : "secondary"}
              aria-pressed={h.id === hub.id}
              onPress={() => void setParam(h.id)}
            >
              {h.name}
            </Button>
          ))}
        </fieldset>
      )}

      <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-2">
          <PolygonEditor
            areas={areas}
            selected={hub.id}
            tool={hub.service_area ? tool : "draw"}
            onTool={setTool}
            height={560}
            labels={{
              draw: "Draw area",
              edit: "Move points",
              remove: "Delete area",
              save: "Save service area",
              newArea: "New service area",
            }}
            onSave={(_, ring) => void save(ring)}
            onRemove={() => {
              if (hub.service_area) void save(null);
              else toast("There is no service area to delete yet.");
            }}
          />
          {!hub.service_area && !limited && (
            <p role="status" className="rounded-sm bg-accent-soft px-3 py-2.5 text-sm leading-5">
              {hub.name} has no service area yet. Draw it on the map and save, so we know which
              societies it can serve.
            </p>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Panel label={`${hub.name} details`}>
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="text-[17px] leading-6 font-bold">{hub.name}</h2>
              {hub.status === "planned" && <Badge tone="accent">Planned</Badge>}
            </div>
            {!limited && (
              <div>
                <KV k="Address">{hub.address}</KV>
                <KV k="Franchise owner">{hub.franchise_owner?.name ?? "Not assigned yet"}</KV>
                <KV k="Hub manager">{hub.hub_manager?.name ?? "Not assigned yet"}</KV>
                <KV k="Delivery slots">
                  {hub.slots.length ? hub.slots.join(" · ") : "Not set yet"}
                </KV>
                <KV k="Cut-off">{hub.cutoff}</KV>
              </div>
            )}
          </Panel>

          <Panel label="Societies in this area">
            <h2 className="text-[17px] leading-6 font-bold">Societies in this area</h2>
            {societies === null ? (
              <p className="text-sm leading-5 text-fg-secondary">
                Which societies a hub serves is not available yet.
              </p>
            ) : rows.length === 0 ? (
              <p className="text-sm leading-5 text-fg-secondary">
                No societies on this hub yet. Add one in Societies and choose this hub.
              </p>
            ) : (
              <DataTable
                columns={COLUMNS}
                rows={rows}
                dense
                caption={`Societies served by ${hub.name}`}
              />
            )}
            {outside.length > 0 && (
              <p className="text-sm leading-5 text-fg-secondary">
                {listOf(outside.map((s) => `${s.name} (${s.area})`))}{" "}
                {outside.length === 1 ? "is" : "are"} outside every hub's area, so{" "}
                {outside.length === 1 ? "it has" : "they have"} no hub yet.
              </p>
            )}
          </Panel>
        </div>
      </div>

      <AddHub open={adding} onOpenChange={setAdding} onCreated={(id) => void setParam(id)} />
    </>
  );
}

/**
 * The "Add hub" sheet: a name and an address. The new hub is planned until its area and people are set.
 * @param {{ open: boolean, onOpenChange: (open: boolean) => void, onCreated: (id: string) => void }} props
 */
function AddHub({ open, onOpenChange, onCreated }) {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [fields, setFields] = useState(/** @type {Record<string, string>} */ ({}));
  const [problem, setProblem] = useState("");
  const [busy, run] = useTransition();
  const intent = useRef({ sig: "", key: "" });

  function add() {
    /** @type {Record<string, string>} */
    const errors = {};
    if (!name.trim()) errors.name = "Enter the hub's name";
    if (!address.trim()) errors.address = "Enter the hub's address";
    setFields(errors);
    if (Object.keys(errors).length > 0) return;
    setProblem("");
    const sig = JSON.stringify([name.trim(), address.trim()]);
    if (intent.current.sig !== sig) intent.current = { sig, key: crypto.randomUUID() };
    run(async () => {
      const result = await createHub(intent.current.key, { name, address });
      if (!result.ok) {
        setFields(result.fields);
        setProblem(result.message);
        return;
      }
      intent.current = { sig: "", key: "" };
      toast(`${name.trim()} added`);
      setName("");
      setAddress("");
      onOpenChange(false);
      onCreated(result.data.id);
    });
  }

  return (
    <Sheet
      title="Add a hub"
      description="It starts as planned. Draw its service area on the map once it is added."
      open={open}
      onOpenChange={onOpenChange}
      footer={
        <>
          <Button onPress={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" isDisabled={busy} onPress={add}>
            Add hub
          </Button>
        </>
      }
    >
      <TextField
        label="Hub name"
        placeholder="Panvel hub"
        value={name}
        onChange={setName}
        isInvalid={!!fields.name}
        errorMessage={fields.name}
        className="max-w-none"
      />
      <TextField
        label="Address"
        value={address}
        onChange={setAddress}
        isInvalid={!!fields.address}
        errorMessage={fields.address}
        className="max-w-none"
      />
      {problem && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {problem}
        </p>
      )}
    </Sheet>
  );
}
