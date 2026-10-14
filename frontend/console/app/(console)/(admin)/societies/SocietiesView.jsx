"use client";

import { Badge, Button, EmptyState, Select, TextField, toast } from "@gharbasket/ui";
import { PolygonEditor } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { polygonOf, ringOf } from "@/lib/admin/geo.js";
import "@/lib/maps.js";
import { createSociety, updateSociety } from "./actions.js";

/** @typedef {import("@/lib/admin/types.js").Society} Society */

/** What to say when the society list holds only what the public API gives (the admin list is not available). */
const LIMITED_NOTE =
  "Boundaries, hubs and editing societies are not available yet. They need endpoints that have not shipped, so this shows the public list with each society’s towers and flats.";

const STATUS_LABEL = /** @type {const} */ ({
  live: "Live",
  unlocking: "Unlocking",
  prospect: "Prospect",
  paused: "Paused",
});
const STATUS_TONE = /** @type {const} */ ({
  live: "primary",
  unlocking: "accent",
  prospect: "neutral",
  paused: "outline",
});
const STATUS_OPTIONS = Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }));

/**
 * The second line of a list row, as in the design. `limited`: only the selected society has its towers,
 * flats and threshold, so the others show just their area.
 * @param {Society} s @param {boolean} limited
 */
function meta(s, limited) {
  if (limited)
    return [
      s.towers.length ? `${s.towers.length} wings` : null,
      s.flats ? `${s.flats} flats` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  if (s.status === "unlocking") return `unlocks at ${s.unlock_threshold}`;
  if (!s.boundary) return "no boundary yet";
  const wings = s.towers.length ? `${s.towers.length} wings` : null;
  const flats = s.flats ? `${s.flats} flats` : null;
  return [wings, flats].filter(Boolean).join(" · ") || "details to add";
}

/**
 * The societies map editor: the list of societies on the left, the map with the boundary tools in
 * the middle, and the selected society's details on the right.
 * @param {{ hubName: string, societies: Society[], hubs: { id: string, name: string }[], limited: boolean }} props
 * `limited`: the admin list (a @proposal, not in the contract yet) is not available, so societies come from the
 * public list and the page is read-only
 */
export function SocietiesView({ hubName, societies, hubs, limited }) {
  const [selectedId, setSelectedId] = useQueryState(
    "society",
    // shallow false: without the admin list the server loads the selected society's towers and flats
    parseAsString.withOptions({ history: "replace", shallow: false }),
  );
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState(
    /** @type {import("@/lib/admin/geo.js").LngLat[] | null} */ (null),
  );
  const adding = selectedId === "new";
  const selected = adding
    ? null
    : (societies.find((s) => s.id === selectedId) ?? societies[0] ?? null);
  const [tool, setTool] = useState(
    /** @type {"draw" | "edit"} */ (selected?.boundary ? "edit" : "draw"),
  );
  const editorId = adding ? "new" : (selected?.id ?? "new");
  const q = search.trim().toLowerCase();
  const shown = societies.filter((s) => !q || `${s.name} ${s.area}`.toLowerCase().includes(q));

  if (societies.length === 0 && !adding) {
    return (
      <EmptyState
        title="No societies yet"
        body="Add the first society, then draw its boundary on the map."
        action="Add society"
        onAction={() => setSelectedId("new")}
      />
    );
  }

  const areas = societies
    .filter((s) => s.boundary)
    .map((s) => ({
      id: s.id,
      label: s.name,
      status: /** @type {"live" | "unlocking" | "prospect"} */ (
        s.status === "paused" ? "prospect" : s.status
      ),
      coordinates: ringOf(s.boundary),
    }));

  /** @param {string} id */
  function pick(id) {
    setDraft(null);
    setTool(societies.find((s) => s.id === id)?.boundary ? "edit" : "draw");
    setSelectedId(id);
  }

  /**
   * The editor's own Save: stored straight away for a society that exists, kept for a new one.
   * @param {import("@/lib/admin/geo.js").LngLat[]} ring
   */
  async function saveBoundary(ring) {
    if (adding || !selected) {
      setDraft(ring);
      toast("Boundary kept. Save the society to store it.");
      return;
    }
    const before = selected.boundary;
    const result = await updateSociety(selected.id, { boundary: polygonOf(ring) });
    if (!result.ok) return toast(result.message);
    toast("Boundary saved", {
      action: {
        label: "Undo",
        onClick: () =>
          void updateSociety(/** @type {string} */ (selected.id), { boundary: before }),
      },
    });
  }

  async function removeBoundary() {
    if (adding || !selected) {
      setDraft(null);
      return;
    }
    if (!selected.boundary) return toast("There is no boundary to delete yet.");
    if (selected.status === "live")
      return toast("A live society needs its boundary. Change its status first.");
    const before = selected.boundary;
    const result = await updateSociety(selected.id, { boundary: null });
    if (!result.ok) return toast(result.message);
    toast("Boundary deleted", {
      action: {
        label: "Undo",
        onClick: () =>
          void updateSociety(/** @type {string} */ (selected.id), { boundary: before }),
      },
    });
  }

  return (
    <>
      {limited && (
        <p
          role="status"
          className="rounded-md border border-line bg-surface px-3.5 py-3 text-sm leading-5"
        >
          {LIMITED_NOTE}
        </p>
      )}
      <div className="grid overflow-hidden rounded-md border border-line min-[1100px]:grid-cols-[280px_minmax(0,1fr)_340px]">
        <section
          aria-label={`Societies of ${hubName}`}
          className="flex flex-col gap-1.5 border-b border-line bg-surface p-4 min-[1100px]:border-r min-[1100px]:border-b-0"
        >
          <TextField
            label="Find a society"
            placeholder="Name or sector"
            value={search}
            onChange={setSearch}
            className="max-w-none"
          />
          <ul className="m-0 mt-1 flex list-none flex-col gap-0.5 p-0">
            {shown.map((s) => {
              const current = !adding && selected?.id === s.id;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    aria-current={current}
                    onClick={() => pick(s.id)}
                    className={`flex min-h-12 w-full cursor-default flex-col gap-0.5 rounded-sm border px-3 py-2.5 text-left outline-none focus-visible:shadow-focus ${current ? "border-fg bg-bg" : "border-transparent"}`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <strong>{s.name}</strong>
                      <Badge tone={STATUS_TONE[s.status]}>{STATUS_LABEL[s.status]}</Badge>
                    </span>
                    <span className="text-sm leading-5 text-fg-secondary">
                      {[s.area, meta(s, limited)].filter(Boolean).join(" · ")}
                    </span>
                  </button>
                </li>
              );
            })}
            {shown.length === 0 && (
              <li className="px-3 py-2 text-sm text-fg-secondary">
                No society matches that. Try a sector number or part of the name.
              </li>
            )}
          </ul>
          {!limited && (
            <Button variant="secondary" block onPress={() => pick("new")} className="mt-2">
              Add society
            </Button>
          )}
        </section>

        <section aria-label="Map" className="flex min-w-0 flex-col gap-2 p-4">
          <PolygonEditor
            areas={areas}
            selected={editorId}
            tool={tool}
            onTool={setTool}
            height={560}
            onSave={(_, ring) => void saveBoundary(ring)}
            onRemove={() => void removeBoundary()}
            onCancel={() => setDraft(null)}
          />
          {draft && (
            <p role="status" className="text-sm leading-5 text-fg-secondary">
              A boundary of {draft.length} corners is ready. Save the society to store it.
            </p>
          )}
        </section>

        <aside
          aria-label="Society details"
          className="flex min-w-0 flex-col gap-3.5 border-t border-line p-4 min-[1100px]:border-t-0 min-[1100px]:border-l"
        >
          <SocietyForm
            key={adding ? "new" : `${selected?.id}:${selected?.version}`}
            society={adding ? null : selected}
            hubs={hubs}
            draft={draft}
            readOnly={limited}
            onCreated={(id) => {
              setDraft(null);
              void setSelectedId(id);
            }}
          />
        </aside>
      </div>
    </>
  );
}

/**
 * The details form of one society (or an empty one for "Add society"). Keyed by society and version,
 * so it starts again from the saved values whenever those change.
 * @param {{ society: Society | null, hubs: { id: string, name: string }[],
 *   draft: import("@/lib/admin/geo.js").LngLat[] | null, readOnly: boolean, onCreated: (id: string) => void }} props
 */
function SocietyForm({ society, hubs, draft, readOnly, onCreated }) {
  const [name, setName] = useState(society?.name ?? "");
  const [area, setArea] = useState(society?.area ?? "");
  const [hub, setHub] = useState(society?.hub_id ?? "none");
  const [status, setStatus] = useState(society?.status ?? "prospect");
  const [threshold, setThreshold] = useState(String(society?.unlock_threshold ?? 20));
  const [flats, setFlats] = useState(society?.flats ? String(society.flats) : "");
  const [towers, setTowers] = useState(
    /** @type {{ id?: string, label: string }[]} */ (society?.towers ?? []),
  );
  const [problem, setProblem] = useState(/** @type {string | null} */ (null));
  const [fields, setFields] = useState(/** @type {Record<string, string>} */ ({}));
  const [busy, startSave] = useTransition();
  const intent = useRef({ sig: "", key: "" });

  const creating = society === null;
  const boundaryCorners = ringOf(society?.boundary ?? null).length;
  const hubOptions = [
    { value: "none", label: "Not on a hub yet" },
    ...hubs.map((h) => ({ value: h.id, label: h.name })),
  ];

  function save() {
    /** @type {Record<string, string>} */
    const errors = {};
    const t = Number(threshold);
    if (!Number.isInteger(t) || t < 1 || t > 500)
      errors.unlock_threshold = "Use a whole number from 1 to 500";
    const f = flats.trim() === "" ? null : Number(flats);
    if (f !== null && (!Number.isInteger(f) || f < 1 || f > 5000))
      errors.flats = "Use a whole number, or leave it empty";
    if (creating && !name.trim()) errors.name = "Enter the society's name";
    if (creating && !area.trim()) errors.area = "Enter the sector or area";
    setFields(errors);
    if (Object.keys(errors).length > 0) return setProblem("Check the fields marked below.");
    setProblem(null);
    const body = {
      hub_id: hub === "none" ? null : hub,
      status,
      unlock_threshold: t,
      flats: f,
      towers: towers.filter((x) => x.label.trim()),
    };
    startSave(async () => {
      if (creating) {
        // One key per distinct request: the same key with an edited form would be refused.
        const sig = JSON.stringify({ ...body, name, area });
        if (intent.current.sig !== sig) intent.current = { sig, key: crypto.randomUUID() };
        const result = await createSociety(intent.current.key, { ...body, name, area });
        if (!result.ok) {
          setFields(result.fields);
          return setProblem(result.message);
        }
        intent.current = { sig: "", key: "" };
        if (draft) await updateSociety(result.data.id, { boundary: polygonOf(draft) });
        toast(`${name.trim()} added`);
        onCreated(result.data.id);
      } else {
        const result = await updateSociety(society.id, body);
        if (!result.ok) {
          setFields(result.fields);
          return setProblem(result.message);
        }
        toast(`${society.name} saved`);
      }
    });
  }

  return (
    <>
      <div className="flex flex-col gap-0.5">
        <h2 className="text-[17px] leading-6 font-bold">
          {creating ? "New society" : society.name}
        </h2>
        <p className="text-sm leading-5 text-fg-secondary">
          {creating
            ? "Draw its boundary on the map, then fill in the details."
            : `${society.area}${society.hub_id || readOnly ? "" : " · not on a hub yet"}`}
        </p>
      </div>

      {creating && (
        <>
          <TextField
            label="Society name"
            value={name}
            onChange={setName}
            isInvalid={!!fields.name}
            errorMessage={fields.name}
            className="max-w-none"
          />
          <TextField
            label="Sector or area"
            value={area}
            onChange={setArea}
            isInvalid={!!fields.area}
            errorMessage={fields.area}
            className="max-w-none"
          />
        </>
      )}

      {!readOnly && (
        <p
          role="status"
          className={`rounded-sm px-3 py-2.5 text-sm leading-5 ${boundaryCorners > 0 ? "bg-primary-soft" : "bg-accent-soft"}`}
        >
          {boundaryCorners > 0
            ? `Boundary saved, ${boundaryCorners} corners. Addresses are checked against it.`
            : draft
              ? `A boundary of ${draft.length} corners is ready. Save the society to store it.`
              : "No boundary saved yet, so addresses here are accepted anywhere. Close the shape and save to start checking them."}
        </p>
      )}

      {!readOnly && <Select label="Hub" options={hubOptions} value={hub} onChange={setHub} />}
      <Select
        label="Status"
        options={STATUS_OPTIONS}
        value={status}
        onChange={(v) => setStatus(/** @type {typeof status} */ (v))}
      />
      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="Homes to unlock"
          inputMode="numeric"
          value={threshold}
          onChange={setThreshold}
          isInvalid={!!fields.unlock_threshold}
          errorMessage={fields.unlock_threshold}
          className="max-w-none"
        />
        <TextField
          label="Flats"
          inputMode="numeric"
          placeholder="Not known"
          value={flats}
          onChange={setFlats}
          isInvalid={!!fields.flats}
          errorMessage={fields.flats}
          className="max-w-none"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="font-semibold">Towers</span>
        {towers.length === 0 && (
          <p className="text-sm leading-5 text-fg-secondary">
            None yet. Residents pick a tower when they add their flat.
          </p>
        )}
        {towers.map((t, i) => (
          <div key={t.id ?? `new-${i}`} className="flex items-end gap-2">
            <TextField
              label={`Tower ${i + 1}`}
              value={t.label}
              onChange={(v) => setTowers(towers.map((x, j) => (j === i ? { ...x, label: v } : x)))}
              isDisabled={readOnly}
              className="max-w-none flex-1"
            />
            <Button
              variant="quiet"
              aria-label={`Remove tower ${i + 1}`}
              isDisabled={readOnly}
              onPress={() => setTowers(towers.filter((_, j) => j !== i))}
            >
              Remove
            </Button>
          </div>
        ))}
        <Button
          variant="quiet"
          isDisabled={readOnly}
          onPress={() =>
            setTowers([...towers, { label: `Wing ${String.fromCharCode(65 + towers.length)}` }])
          }
          className="-ml-4 self-start"
        >
          Add tower
        </Button>
      </div>

      {problem && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {problem}
        </p>
      )}
      <Button
        variant="primary"
        block
        isDisabled={busy || readOnly}
        onPress={save}
        className="mt-auto"
      >
        {creating ? "Add society" : "Save society"}
      </Button>
    </>
  );
}
