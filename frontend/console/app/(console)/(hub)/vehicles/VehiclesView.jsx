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
  { key: "tiffins", label: "Max tiffins", align: /** @type {const} */ ("end") },
  { key: "today", label: "Today" },
  { key: "state", label: "State" },
];

/** @type {Record<"on_route" | "available" | "retired", { tone: "primary" | "outline" | "neutral", label: string }>} */
const STATE = {
  on_route: { tone: "primary", label: "On route" },
  available: { tone: "outline", label: "Available" },
  retired: { tone: "neutral", label: "Retired" },
};

/**
 * @param {{ vehicles: import("./data.js").Vehicle[], today: import("./data.js").TodayRoute[], riders: { user_id: string, name: string }[] }} props
 */
export function VehiclesView({ vehicles, today, riders }) {
  const rows = vehicles.map((v) => {
    const route = today.find((r) => r.vehicleId === v.id);
    const state = !v.active
      ? "retired"
      : route?.status === "in_progress"
        ? "on_route"
        : "available";
    const rider = route ? route.rider : (v.default_rider?.name ?? null);
    return {
      id: v.id,
      name: { text: v.label, sub: v.registration ?? undefined, strong: true },
      kind: KINDS.find((k) => k.value === v.kind)?.label ?? v.kind,
      kg: `${v.capacity_kg} kg`,
      crates: String(v.capacity_crates),
      tiffins: String(v.capacity_tiffins),
      today: route
        ? `${rider ?? "No rider yet"} · trip ${route.trip}`
        : v.active
          ? (rider ?? "—")
          : "—",
      state: { badge: STATE[state] },
    };
  });
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
          footer="A change applies to the next solve, never to a route already published."
          labels={{ empty: "No vehicles yet. Add the first one." }}
        />
        <AddVehicle riders={riders} />
      </div>
      <Toasts />
    </>
  );
}

/** A blank number field means "the default for this kind". @param {string} text */
const optionalWhole = (text) => (text.trim() === "" ? undefined : Number(text));

/** @param {{ riders: { user_id: string, name: string }[] }} props */
function AddVehicle({ riders }) {
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState("cargo_3w");
  const [registration, setRegistration] = useState("");
  const [kg, setKg] = useState("");
  const [crates, setCrates] = useState("");
  const [tiffins, setTiffins] = useState("");
  const [rider, setRider] = useState("");
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
        label: label.trim(),
        kind,
        ...(registration.trim() && { registration: registration.trim() }),
        ...(kg.trim() && { capacity_kg: optionalWhole(kg) }),
        ...(crates.trim() && { capacity_crates: optionalWhole(crates) }),
        ...(tiffins.trim() && { capacity_tiffins: optionalWhole(tiffins) }),
        ...(rider && { default_rider_user_id: rider }),
      };
      const body = JSON.stringify(input);
      if (attempt.current.body !== body) attempt.current = { body, key: newKey() };
      const result = await addVehicle(attempt.current.key, input);
      if (result.ok) {
        toast(`${result.label} added`);
        for (const reset of [setLabel, setRegistration, setKg, setCrates, setTiffins, setRider])
          reset("");
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
      <TextField
        label="Label"
        placeholder="EV-3"
        value={label}
        onChange={edit("label", setLabel)}
        isInvalid={!!fields.label}
        errorMessage={fields.label}
      />
      <Select label="Kind" options={KINDS} value={kind} onChange={setKind} />
      <TextField
        label="Registration number (optional)"
        placeholder="MH46AB1234"
        value={registration}
        onChange={edit("registration", setRegistration)}
        isInvalid={!!fields.registration}
        errorMessage={fields.registration}
      />
      <div className="grid grid-cols-3 gap-3">
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
        <TextField
          label="Max tiffins"
          inputMode="numeric"
          value={tiffins}
          onChange={edit("capacity_tiffins", setTiffins)}
          isInvalid={!!fields.capacity_tiffins}
          errorMessage={fields.capacity_tiffins}
        />
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        Leave the limits blank to use the kind’s usual ones (three-wheeler 400 kg and 24 crates,
        two-wheeler 40 kg and 30 tiffins).
      </p>
      <Select
        label="Default rider (optional)"
        placeholder="No default rider"
        options={riders.map((r) => ({ value: r.user_id, label: r.name }))}
        value={rider}
        onChange={edit("default_rider_user_id", setRider)}
        error={fields.default_rider_user_id}
      />
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
