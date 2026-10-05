"use client";

import { Button, Select, Sheet, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useRef, useState, useTransition } from "react";
import { PageHeader, Panel } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { addNgo, createDrive } from "./actions.js";

/** @typedef {import("@/mocks/community.js").Drive} Drive */
/** @typedef {import("@/mocks/community.js").PointRate} PointRate */
/** @typedef {import("@/mocks/community.js").Ngo} Ngo */
/** @typedef {import("@/lib/types.js").Column} Column */

/** @type {Column[]} */
const DRIVE_COLUMNS = [
  { key: "name", label: "Drive" },
  { key: "dates", label: "Dates" },
  { key: "mult", label: "Multiplier", align: "end" },
  { key: "state", label: "State" },
];
/** @type {Column[]} */
const RATE_COLUMNS = [
  { key: "item", label: "Item" },
  { key: "unit", label: "Counted by" },
  { key: "pts", label: "Base points", align: "end" },
];
/** @type {Column[]} */
const NGO_COLUMNS = [
  { key: "name", label: "NGO" },
  { key: "contact", label: "Coordinator" },
  { key: "state", label: "State" },
];

const STATE = {
  running: { tone: /** @type {const} */ ("primary"), label: "Running" },
  upcoming: { tone: /** @type {const} */ ("accent"), label: "Starts soon" },
  ended: { tone: /** @type {const} */ ("outline"), label: "Ended" },
};

const MULTIPLIERS = [
  { value: "1.5", label: "1.5×" },
  { value: "2", label: "2×" },
  { value: "3", label: "3×" },
];

/** "1 to 31 Oct", "10 to 30 Jun", "28 Oct to 5 Nov". @param {string} from @param {string} to */
function range(from, to) {
  const fmt = (/** @type {string} */ iso, /** @type {boolean} */ withMonth) =>
    new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-IN", {
      day: "numeric",
      ...(withMonth ? { month: "short" } : {}),
      timeZone: "UTC",
    });
  const sameMonth = from.slice(0, 7) === to.slice(0, 7);
  return `${fmt(from, !sameMonth)} to ${fmt(to, true)}`;
}

/** @param {number} m */
const times = (m) => `${m}×`;

/** @param {{ drives: Drive[], rates: PointRate[], ngos: Ngo[] }} props */
export function DrivesView({ drives, rates, ngos }) {
  const [driveOpen, setDriveOpen] = useState(false);
  const [ngoOpen, setNgoOpen] = useState(false);
  const running = drives.find((d) => d.state === "running");
  // The worked example of the footer, from the live rates: a school bag and 3 kg of clothes.
  const bag = rates.find((r) => r.key === "school_bag");
  const clothes = rates.find((r) => r.key === "clothes");
  const example =
    running && bag && clothes
      ? `With these, a school bag and 3 kg of clothes in the ${running.name} earn ${(bag.points + 3 * clothes.points) * running.multiplier} points.`
      : "";

  return (
    <>
      <PageHeader title="Drives and NGOs">
        A drive multiplies Kindness points for a while. Donations ride the van’s return leg, the hub
        checks them, and the NGO records where they went.
      </PageHeader>
      <Toasts />
      <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Panel
            title="Drives"
            aside={<Button onPress={() => setDriveOpen(true)}>New drive</Button>}
          >
            <DataTable
              dense
              caption="Donation drives"
              columns={DRIVE_COLUMNS}
              rows={drives.map((d) => ({
                id: d.id,
                name: { text: d.name, sub: d.description, strong: true },
                dates: range(d.starts_on, d.ends_on),
                mult: times(d.multiplier),
                state: { badge: STATE[d.state ?? "running"] },
              }))}
              labels={{ empty: "No drives yet. Start one for the next festival." }}
            />
          </Panel>
          <Panel title="Points per item">
            <DataTable
              dense
              caption="Base points per item before the drive multiplier"
              columns={RATE_COLUMNS}
              rows={rates.map((r) => ({
                id: r.key,
                item: r.label,
                unit: r.unit === "kg" ? "per kg" : "each",
                pts: r.points,
              }))}
              footer={`The rates are provisional until the team settles points per item. ${example}`}
            />
          </Panel>
        </div>
        <Panel title="NGOs">
          <DataTable
            dense
            caption="Partner NGOs"
            columns={NGO_COLUMNS}
            rows={ngos.map((n) => ({
              id: n.id,
              name: { text: n.name, sub: n.note ?? undefined, strong: true },
              contact: n.coordinator ?? "—",
              state: {
                badge: n.verified
                  ? { tone: "primary", label: "Verified" }
                  : { tone: "outline", label: "Unverified" },
              },
            }))}
            labels={{ empty: "No NGOs yet." }}
          />
          <p className="text-sm leading-5 text-fg-secondary">
            Rhythm and Manish are identifying the two pilot NGOs. A new NGO stays unverified until
            its registration is checked; unverified NGOs can’t receive hand-offs.
          </p>
          <Button block onPress={() => setNgoOpen(true)}>
            Add NGO
          </Button>
        </Panel>
      </div>
      <DriveSheet open={driveOpen} onOpenChange={setDriveOpen} />
      <NgoSheet open={ngoOpen} onOpenChange={setNgoOpen} />
    </>
  );
}

/** @param {{ open: boolean, onOpenChange: (open: boolean) => void }} props */
function DriveSheet({ open, onOpenChange }) {
  const [pending, start] = useTransition();
  const key = useRef(crypto.randomUUID());
  const [form, setForm] = useState({
    name: "",
    description: "",
    starts_on: "",
    ends_on: "",
    multiplier: "2",
  });
  const [problem, setProblem] = useState(
    /** @type {{ message: string, fields: Record<string, string> } | null} */ (null),
  );
  /** @param {Partial<typeof form>} patch */
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const err = (/** @type {string} */ f) => problem?.fields[f];

  function submit() {
    setProblem(null);
    start(async () => {
      const res = await createDrive({ ...form, multiplier: Number(form.multiplier) }, key.current);
      if (!res.ok) {
        setProblem({ message: res.message, fields: res.fields ?? {} });
        return;
      }
      key.current = crypto.randomUUID();
      setForm({ name: "", description: "", starts_on: "", ends_on: "", multiplier: "2" });
      onOpenChange(false);
      toast(`${form.name.trim()} created`);
    });
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="New drive"
      description="Points for every donation in these dates are multiplied."
      footer={
        <>
          <Button variant="quiet" onPress={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" isDisabled={pending} onPress={submit}>
            Create drive
          </Button>
        </>
      }
    >
      <TextField
        label="Name"
        value={form.name}
        onChange={(name) => set({ name })}
        isInvalid={!!err("name")}
        errorMessage={err("name")}
        placeholder="e.g. Winter clothes drive"
      />
      <TextField
        label="What it asks for"
        value={form.description}
        onChange={(description) => set({ description })}
        placeholder="e.g. Blankets, sweaters, shoes"
      />
      <TextField
        label="Starts"
        type="date"
        value={form.starts_on}
        onChange={(starts_on) => set({ starts_on })}
        isInvalid={!!err("starts_on")}
        errorMessage={err("starts_on")}
      />
      <TextField
        label="Ends"
        type="date"
        value={form.ends_on}
        onChange={(ends_on) => set({ ends_on })}
        isInvalid={!!err("ends_on")}
        errorMessage={err("ends_on")}
      />
      <Select
        label="Points multiplier"
        options={MULTIPLIERS}
        value={form.multiplier}
        onChange={(multiplier) => set({ multiplier })}
      />
      {problem && !Object.keys(problem.fields).length && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {problem.message}
        </p>
      )}
    </Sheet>
  );
}

/** @param {{ open: boolean, onOpenChange: (open: boolean) => void }} props */
function NgoSheet({ open, onOpenChange }) {
  const [pending, start] = useTransition();
  const key = useRef(crypto.randomUUID());
  const [name, setName] = useState("");
  const [coordinator, setCoordinator] = useState("");
  const [error, setError] = useState(/** @type {string | null} */ (null));

  function submit() {
    setError(null);
    start(async () => {
      const res = await addNgo({ name, coordinator }, key.current);
      if (!res.ok) {
        setError(res.fields?.name ?? res.message);
        return;
      }
      key.current = crypto.randomUUID();
      onOpenChange(false);
      toast(`${name.trim()} added, not verified yet`);
      setName("");
      setCoordinator("");
    });
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Add NGO"
      description="It stays unverified until its registration is checked."
      footer={
        <>
          <Button variant="quiet" onPress={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" isDisabled={pending} onPress={submit}>
            Add NGO
          </Button>
        </>
      }
    >
      <TextField
        label="NGO name"
        value={name}
        onChange={setName}
        isInvalid={!!error}
        errorMessage={error ?? undefined}
      />
      <TextField
        label="Coordinator"
        value={coordinator}
        onChange={setCoordinator}
        placeholder="Who we speak to"
      />
    </Sheet>
  );
}
