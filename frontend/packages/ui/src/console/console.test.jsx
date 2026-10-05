import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { cellSortValue, DataTable } from "./DataTable.jsx";

const columns = [
  { key: "name", label: "Item", sortable: true },
  { key: "price", label: "Price", sortable: true, align: /** @type {const} */ ("end") },
  { key: "state", label: "State" },
];
const rows = [
  {
    id: "a",
    name: { text: "Tomato", strong: true, sub: "Local" },
    price: { money: 4500 },
    state: { status: /** @type {const} */ ("ready"), text: "Ready" },
  },
  {
    id: "b",
    name: { text: "Okra", href: "/okra" },
    price: { money: 12000 },
    state: { badge: { tone: /** @type {const} */ ("accent"), label: "New" } },
  },
  { id: "c", name: "Bitter gourd", price: { money: 900 }, state: null },
];
/** Item names in the order they appear in the rendered table. */
const order = (/** @type {string} */ html) =>
  ["Tomato", "Okra", "Bitter gourd"].sort((x, y) => html.indexOf(x) - html.indexOf(y));

describe("cellSortValue", () => {
  it("uses sort, money, text or badge label, in that order", () => {
    expect(cellSortValue({ text: "x", sort: 3 })).toBe(3);
    expect(cellSortValue({ money: 500 })).toBe(500);
    expect(cellSortValue({ text: "Okra" })).toBe("Okra");
    expect(cellSortValue({ badge: { label: "New" } })).toBe("New");
    expect(cellSortValue(null)).toBe("");
  });
});

describe("DataTable", () => {
  it("keeps the given order until a sort is chosen", () => {
    expect(order(renderToStaticMarkup(<DataTable columns={columns} rows={rows} />))).toEqual([
      "Tomato",
      "Okra",
      "Bitter gourd",
    ]);
  });

  it("sorts text naturally, ascending", () => {
    const html = renderToStaticMarkup(
      <DataTable columns={columns} rows={rows} defaultSort={{ key: "name", dir: "asc" }} />,
    );
    expect(order(html)).toEqual(["Bitter gourd", "Okra", "Tomato"]);
    expect(html).toContain('aria-sort="ascending"');
  });

  it("sorts money by amount, descending, not by its text", () => {
    const html = renderToStaticMarkup(
      <DataTable columns={columns} rows={rows} defaultSort={{ key: "price", dir: "desc" }} />,
    );
    expect(order(html)).toEqual(["Okra", "Tomato", "Bitter gourd"]);
    expect(html).toContain('aria-sort="descending"');
  });

  it("does not sort client-side when the server sorts", () => {
    const html = renderToStaticMarkup(
      <DataTable
        columns={columns}
        rows={rows}
        manualSort
        defaultSort={{ key: "name", dir: "asc" }}
      />,
    );
    expect(order(html)).toEqual(["Tomato", "Okra", "Bitter gourd"]);
  });

  it("renders each cell kind", () => {
    const html = renderToStaticMarkup(
      <DataTable columns={columns} rows={rows} caption="Prices" footer="3 of 3" />,
    );
    expect(html).toContain("₹45"); // money from paise
    expect(html).toContain("₹120");
    expect(html).toContain('href="/okra"'); // link
    expect(html).toContain("Local"); // sub line
    expect(html).toContain("Ready"); // status pill word
    expect(html).toContain("New"); // badge
    expect(html).toContain("<caption");
    expect(html).toContain("3 of 3");
  });

  it("shows the empty state, and select-all when selectable", () => {
    expect(
      renderToStaticMarkup(<DataTable columns={columns} rows={[]} labels={{ empty: "No bags" }} />),
    ).toContain("No bags");
    const html = renderToStaticMarkup(
      <DataTable
        columns={columns}
        rows={rows}
        selectable
        defaultSelected={["a"]}
        bulk={<button type="button">Approve</button>}
      />,
    );
    expect(html).toContain("1 selected");
    expect(html).toContain('aria-label="Select row b"');
  });
});
