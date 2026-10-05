import { DataTable } from "./index.js";

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
