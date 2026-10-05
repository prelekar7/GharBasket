"use client";

import { Button, Select, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useRef, useState, useTransition } from "react";
import { KINDS, newKey } from "@/lib/fleet.js";
import { Toasts } from "../_fleet/Toasts.jsx";
import { addVehicle } from "./actions.js";

const COLUMNS = [
  { key: "name", label: "Vehicle", sortable: true },
  { key: "kind", label: "Kind" },
  { key: "kg", label: "Max weight", align: /** @type {const} */ ("end") },
  { key: "crates", label: "Max crates", align: /** @type {const} */ ("end") },
  { key: "today", label: "Today" },
  { key: "state", label: "State" },
];

/** @type {Record<import("./data.js").Vehicle["status"], { tone: "primary" | "outline" | "neutral", label: string }>} */
const STATE = {
  on_route: { tone: "primary", label: "On route" },
  available: { tone: "outline", label: "Available" },
  retired: { tone: "neutral", label: "Retired" },
};

/** @param {{ vehicles: import("./data.js").Vehicle[] }} props */
export function VehiclesView({ vehicles }) {
  const rows = vehicles.map((v) => ({
    id: v.id,
    name: { text: v.name, sub: v.registration, strong: true },
    kind: KINDS.find((k) => k.value === v.kind)?.label ?? v.kind,
    kg: `${v.capacity_kg} kg`,
    crates: String(v.capacity_crates),
    today: v.assignment ? `${v.assignment.rider} · ${v.assignment.route_label}` : "—",
    state: { badge: STATE[v.status] },
  }));
  return (
    <>
      <div className="flex flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">Vehicles</h1>
        <p className="text-sm leading-5 text-fg-secondary">
          The route solver plans within each vehicle’s weight and crate limits. Retired vehicles
          keep their history.
        </p>
      </div>
      <div className="grid items-start gap-4 min-[900px]:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <DataTable
          columns={COLUMNS}
          rows={rows}
          caption="Vehicles at this hub"
          footer="Registration numbers and limits are placeholders until the hub’s real fleet is entered."
          labels={{ empty: "No vehicles yet. Add the first one." }}
        />
        <AddVehicle />
      </div>
      <Toasts />
    </>
  );
}

function AddVehicle() {
  const [kind, setKind] = useState("van");
  const [registration, setRegistration] = useState("");
  const [kg, setKg] = useState("");
  const [crates, setCrates] = useState("");
  const [fields, setFields] = useState(/** @type {Record<string, string>} */ ({}));
  const [problem, setProblem] = useState("");
  // One Idempotency-Key per request: the same body retried reuses it, a changed body is a new intent.
  const attempt = useRef({ body: "", key: "" });
  const [pending, start] = useTransition();

  /** Editing a field clears its message, so the browser does not block the next submit on a stale error. @param {string} name @param {(v: string) => void} set */
  const edit = (name, set) => (/** @type {string} */ v) => {
    set(v);
    setFields(({ [name]: _gone, ...rest }) => rest);
  };

  function submit() {
    start(async () => {
      const input = {
        kind,
        registration,
        capacity_kg: Number(kg),
        capacity_crates: Number(crates),
      };
      const body = JSON.stringify(input);
      if (attempt.current.body !== body) attempt.current = { body, key: newKey() };
      const result = await addVehicle(attempt.current.key, input);
      if (result.ok) {
        toast(`${result.name} added`);
        setRegistration("");
        setKg("");
        setCrates("");
        setFields({});
        setProblem("");
        attempt.current = { body: "", key: "" }; // the next vehicle is a new intent
      } else {
        setFields(result.fields);
        setProblem(result.message);
      }
    });
  }

  return (
    <form
      aria-label="Add a vehicle"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
    >
      <h2 className="text-[17px] leading-6 font-bold">Add a vehicle</h2>
      <Select label="Kind" options={KINDS} value={kind} onChange={setKind} />
      <TextField
        label="Registration number"
        placeholder="MH-46 …"
        value={registration}
        onChange={edit("registration", setRegistration)}
        isInvalid={!!fields.registration}
        errorMessage={fields.registration}
      />
      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="Max weight"
          inputMode="numeric"
          prefix="kg"
          value={kg}
          onChange={edit("capacity_kg", setKg)}
          isInvalid={!!fields.capacity_kg}
          errorMessage={fields.capacity_kg}
        />
        <TextField
          label="Max crates"
          inputMode="numeric"
          value={crates}
          onChange={edit("capacity_crates", setCrates)}
          isInvalid={!!fields.capacity_crates}
          errorMessage={fields.capacity_crates}
        />
      </div>
      {problem && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {problem}
        </p>
      )}
      <Button type="submit" variant="primary" block isDisabled={pending}>
        Add vehicle
      </Button>
    </form>
  );
}
