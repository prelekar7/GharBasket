import { useState } from "react";
import {
  ChartCard,
  DataTable,
  FilterBar,
  KanbanBoard,
  KpiTile,
  MapPanel,
  PolygonEditor,
} from "./index.js";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Console", parameters: { layout: "padded" } };
export default meta;

/** @param {string} id @param {string} bag @param {string} flat @param {import("../components/Status.jsx").Status} status @param {string} proof @param {string} at */
const row = (id, bag, flat, status, proof, at) => ({
  id,
  bag: { text: bag, strong: true },
  flat,
  status: {
    status,
    text: {
      issue: "Issue",
      ready: "In the lobby",
      out: "With the captain",
      delivered: "Delivered",
      planned: "Planned",
      preparing: "Preparing",
    }[status],
  },
  proof: { text: proof },
  total: { money: 28000 + Number(id) * 4000 },
  at,
});
const columns = [
  { key: "bag", label: "Bag", sortable: true },
  { key: "flat", label: "Flat", sortable: true },
  { key: "status", label: "State" },
  { key: "proof", label: "Proof" },
  { key: "total", label: "Total", sortable: true, align: /** @type {const} */ ("end") },
  { key: "at", label: "Time", sortable: true },
];
const rows = [
  {
    ...row("1", "B-1104", "B-1104", "issue", "Photo by Meena tai", "8:31"),
    __tone: /** @type {const} */ ("danger"),
  },
  row("2", "B-230", "C-302", "ready", "No one home, rang twice", "8:44"),
  row("3", "B-221", "B-603", "ready", "Family chose lobby pickup", "8:15"),
  row("4", "K-101", "B-205", "out", "Crate K-1 handed over", "8:42"),
  row("5", "K-105", "A-101", "out", "Methi refunded ₹20 to wallet", "8:42"),
];

export const Table = {
  render: () => (
    <DataTable
      columns={columns}
      rows={rows}
      defaultSort={{ key: "at", dir: "asc" }}
      caption="Bags that are not plain door deliveries"
      footer="5 of 58 bags shown. Example numbers."
    />
  ),
};

export const TableSelectableDense = {
  render: () => (
    <DataTable
      columns={columns}
      rows={rows}
      dense
      selectable
      defaultSelected={["2", "3"]}
      bulk={<span className="text-fg-secondary">Approve or refund</span>}
      caption="Selectable bags"
    />
  ),
};

export const TableEmpty = {
  render: () => (
    <DataTable
      columns={columns}
      rows={[]}
      caption="No bags"
      labels={{ empty: "No bags need attention." }}
    />
  ),
};

const many = Array.from({ length: 2000 }, (_, i) =>
  row(
    String(i + 1),
    `B-${1000 + i}`,
    `A-${(i % 12) + 101}`,
    i % 9 === 0 ? "issue" : "delivered",
    "Door scan",
    `${8 + Math.floor(i / 400)}:${String(i % 60).padStart(2, "0")}`,
  ),
);
export const TableVirtual = {
  render: () => (
    <DataTable
      columns={columns}
      rows={many}
      virtual
      height={360}
      dense
      caption="All bags this month"
      footer="2,000 bags, only the visible rows are in the page."
    />
  ),
};

export const Filters = {
  render: () => {
    const [q, setQ] = useState("");
    const [stop, setStop] = useState("all");
    const [active, setActive] = useState([{ key: "date", label: "Tue 6 Oct · 8 to 10 AM" }]);
    return (
      <FilterBar
        search={{ value: q, onChange: setQ, placeholder: "Search bag or flat" }}
        filters={[
          {
            key: "stop",
            label: "Stop",
            value: stop,
            options: [
              { value: "all", label: "All stops" },
              { value: "1", label: "1 · Sunrise Towers" },
              { value: "2", label: "2 · Krishna Kunj" },
            ],
          },
        ]}
        onFilterChange={(_, v) => setStop(v)}
        active={active}
        onRemove={(k) => setActive(active.filter((a) => a.key !== k))}
        onClear={() => setActive([])}
        count={58}
      />
    );
  },
};

export const Kpis = {
  render: () => (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <KpiTile
        label="Boxes today"
        value="58"
        delta={{ value: "46 Sunrise · 12 Krishna Kunj", direction: "flat" }}
        hint="Locked at the 9 PM cut-off"
      />
      <KpiTile
        label="Waste so far"
        value="0.7 kg"
        delta={{ value: "−0.3 kg", direction: "down", good: true, note: "vs Fri" }}
        hint="Spoiled at receiving"
        spark={[1.4, 1.1, 1.2, 0.9, 1.0, 0.7]}
      />
      <KpiTile
        label="Partner earnings"
        value="₹8,450"
        delta={{ value: "+₹1,120", direction: "up", note: "vs last week" }}
        spark={[6200, 6900, 7330, 8450]}
        hint="Week of 28 Sep"
      />
      <KpiTile
        label="Short items"
        value="4"
        delta={{ value: "+3", direction: "up", good: false, note: "vs Fri" }}
        spark={[1, 0, 1, 4]}
      />
    </div>
  ),
};

/** @param {string} id @param {string} title @param {string} meta @param {Record<string, unknown>} [more] */
const card = (id, title, meta, more) => ({ id, title, meta, ...more });
const board = () => [
  {
    id: "todo",
    title: "To pack",
    cards: [
      card("a", "B-217 · C-807", "Sunrise Towers · stop 1 · 16 items"),
      card("b", "B-218 · A-1105", "Sunrise Towers · stop 1 · 13 items"),
    ],
  },
  {
    id: "packing",
    title: "Packing",
    limit: 1,
    cards: [
      card("d", "B-214 · B-402", "12 items", { badge: { tone: "accent", label: "Kavita" } }),
      card("e", "B-215 · A-604", "15 items", { flag: "Methi short" }),
    ],
  },
  {
    id: "packed",
    title: "Packed",
    cards: [
      card("f", "B-213 · A-302", "Sunrise Towers · stop 1", {
        badge: { tone: "primary", label: "Labelled" },
      }),
    ],
  },
  { id: "crated", title: "In crates", cards: [] },
];

export const Kanban = {
  render: () => {
    const [columns, setColumns] = useState(board);
    return (
      <KanbanBoard
        label="Packing board"
        columns={columns}
        onMove={(id, from, to) =>
          setColumns((cols) => {
            const moving = cols.find((c) => c.id === from)?.cards.find((c) => c.id === id);
            return cols.map((c) => ({
              ...c,
              cards:
                c.id === from
                  ? c.cards.filter((x) => x.id !== id)
                  : c.id === to && moving
                    ? [...c.cards, moving]
                    : c.cards,
            }));
          })
        }
      />
    );
  },
};

const weeks = ["28 Sep", "5 Oct", "12 Oct", "19 Oct"];

export const ChartBars = {
  render: () => (
    <div className="grid gap-3 md:grid-cols-2">
      <ChartCard
        title="Active households"
        subtitle="By week · Kharghar hub"
        labels={weeks}
        series={[{ name: "Households", values: [38, 46, 52, 61] }]}
      />
      <ChartCard
        title="Fill rate by week"
        subtitle="Share of ordered quantity delivered · example numbers"
        format="pct"
        max={100}
        labels={weeks}
        series={[
          { name: "Sunrise vendor", values: [92, 88, 95, 97] },
          { name: "Mandi vendor", values: [81, 84, 79, 90] },
        ]}
      />
    </div>
  ),
};

export const ChartLines = {
  render: () => (
    <div className="grid gap-3 md:grid-cols-2">
      <ChartCard
        kind="line"
        title="Partner earnings"
        subtitle="Rupees per week, from the ledger (values in paise)"
        format="money"
        labels={weeks}
        series={[{ name: "Partner earnings", values: [620000, 690000, 733000, 845000] }]}
      />
      <ChartCard
        kind="line"
        title="Forecast vs actual · methi"
        subtitle="Bunches per delivery day, Kharghar hub · simulator"
        labels={["Mon", "Tue", "Wed", "Thu"]}
        series={[
          { name: "Forecast q50", values: [27, 29, 30, 31] },
          { name: "Approved by families", values: [26, 30, 29, 30] },
        ]}
        note="The gap on Tue is the vendor short-delivery, not a forecast miss."
      />
    </div>
  ),
};

/** @type {import("./MapPanel.jsx").Pin[]} */
const pins = [
  { id: "hub", kind: "hub", label: "Kharghar hub", lng: 73.0668, lat: 19.033 },
  {
    id: "sunrise",
    kind: "stop",
    n: 1,
    label: "Sunrise Towers · done 8:07",
    lng: 73.0755,
    lat: 19.045,
  },
  { id: "krishna", kind: "stop", n: 2, label: "Krishna Kunj · ETA 8:40", lng: 73.056, lat: 19.025 },
  { id: "van", kind: "van", label: "Arjun · 8:22", lng: 73.062, lat: 19.039 },
];

export const RouteMap = {
  render: () => (
    <MapPanel
      label="Route 1 on the map"
      markers={pins}
      route={["hub", "sunrise", "krishna", "hub"]}
      legend={[
        { kind: "hub", label: "Hub" },
        { kind: "stop", label: "Stop" },
        { kind: "van", label: "Van now" },
        { kind: "route", label: "Planned route" },
      ]}
      note="The van's position updates every 15 seconds while it is out."
    />
  ),
};

/** @type {import("./PolygonEditor.jsx").Area[]} */
const areaList = [
  {
    id: "kharghar",
    label: "Kharghar hub · service area",
    status: "live",
    coordinates: [
      [73.04, 19.052],
      [73.085, 19.05],
      [73.095, 19.03],
      [73.08, 19.012],
      [73.045, 19.014],
      [73.032, 19.033],
    ],
  },
  {
    id: "taloja",
    label: "Taloja · prospect",
    status: "prospect",
    coordinates: [
      [73.1, 19.06],
      [73.13, 19.06],
      [73.13, 19.04],
      [73.1, 19.04],
    ],
  },
];

export const BoundaryEditor = {
  render: () => {
    const [areas, setAreas] = useState(areaList);
    return (
      <PolygonEditor
        areas={areas}
        selected="kharghar"
        onSave={(id, coordinates) =>
          setAreas((all) => all.map((a) => (a.id === id ? { ...a, coordinates } : a)))
        }
        labels={{ draw: "Draw area", remove: "Delete area", save: "Save service area" }}
      />
    );
  },
};
