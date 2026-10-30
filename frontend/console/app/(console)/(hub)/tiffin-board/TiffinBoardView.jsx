"use client";

import { Button, Checkbox, Dialog, EmptyState, Select, TextField, toast } from "@gharbasket/ui";
import { DataTable, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { formatClock, formatTime, newKey, plural } from "@/lib/fleet.js";
import { dayLabel } from "@/lib/when.js";
import { Toasts } from "../_fleet/Toasts.jsx";
import { assignByHand, runAssignment } from "./actions.js";

const MEAL = { lunch: "Lunch", dinner: "Dinner" };

/** Why a round could not place a tiffin (the furthest rule any kitchen passed), in plain words. @type {Record<string, string>} */
const REASON = {
  pending: "No round has run yet",
  no_skills: "No kitchen has the skills (diet) this tiffin needs",
  below_rating_floor: "The kitchens that can cook it are rated below 3.8",
  out_of_reach: "Every kitchen is more than 5 km from this society",
  no_room: "The kitchens that can cook it are full for this meal",
  no_location: "This society has no map boundary yet, so distance is unknown",
};

const STATE = /** @type {Record<string, string>} */ ({
  assigned: "With the kitchen",
  ready: "Ready",
  picked_up: "Picked up",
  delivered: "Delivered",
  failed: "Not delivered",
  released: "Released",
});

const ASSIGNMENT = /** @type {Record<string, string>} */ ({
  offered: "Offered, waiting",
  accepted: "Accepted",
  preparing: "Cooking",
  ready: "Ready",
  completed: "Done",
  declined: "Declined",
  expired: "No answer",
  cancelled: "Cancelled",
});

/** @param {{ date: string, days: string[], board: import("./page.jsx").TiffinBoard, kitchens: { value: string, label: string }[] }} props */
export function TiffinBoardView({ date, days, board, kitchens }) {
  const [, setDate] = useQueryState("date", parseAsString.withOptions({ shallow: false }));
  const [confirmRun, setConfirmRun] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [busy, start] = useTransition();
  const runKey = useRef(newKey());
  const free = board.meals.reduce((n, m) => n + m.unassigned.length, 0);

  function run() {
    start(async () => {
      const r = await runAssignment(date, runKey.current);
      runKey.current = newKey();
      toast(
        r.ok
          ? `Round run: ${plural(r.data.offers, "offer", "offers")} made, ${r.data.unassigned} still without a kitchen.`
          : r.message,
      );
    });
  }

  return (
    <>
      <PageHead
        title={`Tiffin board · ${dayLabel(date)}`}
        action={
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-[200px]">
              <Select
                label="Day"
                options={days.map((d) => ({ value: d, label: dayLabel(d) }))}
                value={date}
                onChange={(v) => setDate(v)}
              />
            </div>
            <Button isDisabled={busy} onPress={() => setConfirmRun(true)}>
              Run assignment now
            </Button>
            <Button variant="primary" isDisabled={free === 0} onPress={() => setAssigning(true)}>
              Assign by hand
            </Button>
          </div>
        }
      >
        Locked tiffins are offered to kitchens in rounds the evening before. Anything left over can
        be given to a kitchen by hand until 2 hours before she must have it ready.
      </PageHead>

      {assigning && (
        <ManualForm
          date={date}
          board={board}
          kitchens={kitchens}
          onClose={() => setAssigning(false)}
        />
      )}
      {board.meals.length === 0 && (
        <EmptyState
          title="No tiffins for this day"
          body="Tiffins appear here once their orders are locked at the cut-off."
        />
      )}
      {board.meals.map((m) => (
        <MealSection
          key={m.meal}
          meal={m}
          assignments={board.assignments.filter((a) => a.meal === m.meal)}
        />
      ))}

      {board.runs.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-[17px] leading-6 font-bold">Assignment rounds</h2>
          <DataTable
            dense
            caption="Assignment rounds"
            columns={[
              { key: "meal", label: "Meal" },
              { key: "round", label: "Round", align: "end" },
              { key: "how", label: "Chosen by" },
              { key: "at", label: "Run at" },
              { key: "fair", label: "Fairness (Gini)", align: "end" },
              { key: "km", label: "Km per portion", align: "end" },
            ]}
            rows={board.runs.map((r, i) => ({
              id: `${r.meal}-${r.round}-${i}`,
              meal: MEAL[r.meal],
              round: r.round,
              how:
                r.source === "ml" ? `Optimiser (${r.model})` : "Simple fallback (nearest, fairest)",
              at: formatTime(r.at),
              fair: r.gini == null ? "—" : r.gini.toFixed(2),
              km: r.km_per_portion == null ? "—" : r.km_per_portion.toFixed(1),
            }))}
          />
        </section>
      )}

      <Dialog
        title={`Run an assignment round for ${dayLabel(date)}?`}
        confirmLabel="Run round"
        cancelLabel="Cancel"
        isOpen={confirmRun}
        onOpenChange={setConfirmRun}
        onConfirm={run}
      >
        Locked tiffins without a kitchen are offered to the best kitchens now. Tiffins that already
        have a kitchen are left alone.
      </Dialog>
      <Toasts />
    </>
  );
}

/** @param {{ meal: import("./page.jsx").BoardMeal, assignments: import("./page.jsx").Assignment[] }} props */
function MealSection({ meal: m, assignments }) {
  const rows = [
    ...m.unassigned.map((u) => ({
      id: u.order_id,
      code: "—",
      dish: "Tiffin",
      portions: u.portions,
      society: u.society.name,
      state: { badge: { tone: /** @type {const} */ ("danger"), label: "No kitchen" } },
      kitchen: { text: "Nobody yet", sub: REASON[u.reason] ?? u.reason },
    })),
    ...assignments.flatMap((a) =>
      a.orders.map((o) => ({
        id: o.order_id,
        code: { text: o.short_code, strong: true },
        dish: o.product,
        portions: o.portions,
        society: o.society.name,
        state: STATE[o.status] ?? o.status,
        kitchen: {
          text: a.partner.display_name,
          sub: o.status === "released" ? "Taken back" : undefined,
        },
      })),
    ),
  ];
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-[17px] leading-6 font-bold">{MEAL[m.meal]}</h2>
      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <KpiTile
          label="Tiffins"
          value={String(m.orders)}
          hint={plural(m.portions, "portion", "portions")}
        />
        <KpiTile
          label="With a kitchen"
          value={String(m.assigned)}
          hint={`${m.accepted} accepted`}
        />
        <KpiTile
          label="Being cooked"
          value={String(m.preparing + m.ready)}
          hint={`${m.ready} ready`}
        />
        <KpiTile
          label="Picked up"
          value={String(m.picked_up)}
          hint={
            m.unassigned.length
              ? `${m.unassigned.length} without a kitchen`
              : "Every tiffin has a kitchen"
          }
        />
      </div>
      <DataTable
        dense
        caption={`${MEAL[m.meal]} tiffins`}
        columns={[
          { key: "code", label: "Box" },
          { key: "dish", label: "Dish" },
          { key: "portions", label: "Portions", align: "end" },
          { key: "society", label: "Society" },
          { key: "state", label: "State" },
          { key: "kitchen", label: "Kitchen" },
        ]}
        rows={rows}
        labels={{ empty: "No tiffins for this meal." }}
      />
      {assignments.length > 0 && (
        <p className="text-sm leading-5 text-fg-secondary">
          {assignments
            .map(
              (a) =>
                `${a.partner.display_name}: ${plural(a.portions, "portion", "portions")}, ${ASSIGNMENT[a.status] ?? a.status}${a.source === "manual" ? ", by hand" : ""}, ready by ${formatClock(a.ready_by)}`,
            )
            .join(" · ")}
        </p>
      )}
    </section>
  );
}

/** @param {{ date: string, board: import("./page.jsx").TiffinBoard, kitchens: { value: string, label: string }[], onClose: () => void }} props */
function ManualForm({ date, board, kitchens, onClose }) {
  const meals = board.meals.filter((m) => m.unassigned.length > 0);
  const [meal, setMeal] = useState(meals[0]?.meal ?? "lunch");
  const [picked, setPicked] = useState(/** @type {string[]} */ ([]));
  const [kitchen, setKitchen] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, start] = useTransition();
  const key = useRef(newKey());
  const free = meals.find((m) => m.meal === meal)?.unassigned ?? [];

  function submit() {
    if (!picked.length || !kitchen) return setProblem("Pick at least one tiffin and a kitchen.");
    start(async () => {
      const r = await assignByHand(
        { date, order_ids: picked, partner_id: kitchen, note },
        key.current,
      );
      if (!r.ok) return setProblem(r.message);
      toast(
        r.data.over_capacity
          ? "Assigned and accepted. She is now over her usual capacity for this meal."
          : `Assigned and accepted: ${plural(r.data.portions, "portion", "portions")}.`,
      );
      onClose();
    });
  }

  return (
    <form
      aria-label="Assign tiffins by hand"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="flex flex-col gap-3 rounded-md border border-line-strong bg-surface p-4"
    >
      <h2 className="text-[17px] leading-6 font-bold">Assign by hand · {dayLabel(date)}</h2>
      <p className="text-sm leading-5 text-fg-secondary">
        For a kitchen you have already agreed with by phone. It is accepted at once, even if it
        takes her past her capacity. One meal at a time.
      </p>
      <Select
        label="Meal"
        options={meals.map((m) => ({
          value: m.meal,
          label: `${MEAL[m.meal]} · ${m.unassigned.length} waiting`,
        }))}
        value={meal}
        onChange={(v) => {
          setMeal(/** @type {"lunch" | "dinner"} */ (v));
          setPicked([]);
        }}
      />
      <fieldset className="m-0 flex min-w-0 flex-col gap-1 border-0 p-0">
        <legend className="sr-only">Tiffins without a kitchen</legend>
        {free.map((u) => (
          <Checkbox
            key={u.order_id}
            label={`${plural(u.portions, "portion", "portions")} · ${u.society.name}`}
            description={REASON[u.reason] ?? u.reason}
            isSelected={picked.includes(u.order_id)}
            onChange={(on) =>
              setPicked((p) => (on ? [...p, u.order_id] : p.filter((x) => x !== u.order_id)))
            }
          />
        ))}
      </fieldset>
      <Select
        label="Kitchen"
        placeholder="Pick a kitchen"
        options={kitchens}
        value={kitchen}
        onChange={setKitchen}
      />
      <TextField
        label="Note (optional)"
        description="Kept in the audit log."
        value={note}
        onChange={setNote}
      />
      {problem && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {problem}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button onPress={onClose}>Cancel</Button>
        <Button type="submit" variant="primary" isDisabled={busy}>
          Assign {picked.length ? plural(picked.length, "tiffin", "tiffins") : ""}
        </Button>
      </div>
    </form>
  );
}
