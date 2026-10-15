import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChartCard } from "./ChartCard.jsx";
import { cellSortValue, DataTable } from "./DataTable.jsx";
import { FilterBar } from "./FilterBar.jsx";
import { KpiTile, Sparkline } from "./Kpi.jsx";
import { MapPanel } from "./MapPanel.jsx";
import { PolygonEditor } from "./PolygonEditor.jsx";

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

describe("KpiTile", () => {
  it("shows the delta with its note and says the direction in words", () => {
    const html = renderToStaticMarkup(
      <KpiTile
        label="Waste"
        value="0.7 kg"
        delta={{ value: "−0.3 kg", direction: "down", good: true, note: "vs Fri" }}
        hint="Spoiled at receiving"
      />,
    );
    expect(html).toContain("−0.3 kg");
    expect(html).toContain("vs Fri");
    expect(html).toContain("Down, good");
    expect(html).toContain("text-primary");
  });

  it("treats a rise as good unless told otherwise, and a bad change as danger", () => {
    expect(
      renderToStaticMarkup(
        <KpiTile label="a" value="1" delta={{ value: "+2", direction: "up" }} />,
      ),
    ).toContain("text-primary");
    const bad = renderToStaticMarkup(
      <KpiTile label="a" value="1" delta={{ value: "+2", direction: "up", good: false }} />,
    );
    expect(bad).toContain("text-danger");
    expect(bad).toContain("needs attention");
  });

  it("has no arrow when flat, and draws a sparkline only with 2+ points", () => {
    expect(
      renderToStaticMarkup(
        <KpiTile label="a" value="1" delta={{ value: "same", direction: "flat" }} />,
      ),
    ).toContain("No change");
    expect(renderToStaticMarkup(<Sparkline values={[1]} />)).toBe("");
    expect(renderToStaticMarkup(<Sparkline values={[1, 3, 2]} />)).toContain("polyline");
  });
});

describe("FilterBar", () => {
  it("shows the count and removable chips", () => {
    const html = renderToStaticMarkup(
      <FilterBar
        search={{ value: "me", onChange: () => {} }}
        count={6}
        active={[{ key: "d", label: "Tue 6 Oct" }]}
        filters={[
          {
            key: "s",
            label: "Stop",
            value: "all",
            options: [{ value: "all", label: "All stops" }],
          },
        ]}
      />,
    );
    expect(html).toContain("6 results");
    expect(html).toContain('aria-label="Remove filter: Tue 6 Oct"');
    expect(html).toContain("All stops");
  });
});

describe("ChartCard", () => {
  const series = [{ name: "Earnings", values: [620000, 845000] }];
  it("puts the numbers in a table for screen readers, money from paise", () => {
    const html = renderToStaticMarkup(
      <ChartCard
        title="Partner earnings"
        kind="line"
        format="money"
        labels={["28 Sep", "5 Oct"]}
        series={series}
      />,
    );
    expect(html).toContain("<caption>Partner earnings</caption>");
    expect(html).toContain("₹6,200");
    expect(html).toContain("₹8,450");
    expect(html).toContain('aria-hidden="true"'); // the drawing itself is not read aloud
  });

  it("formats percentages and units, and lists several series in a legend", () => {
    const html = renderToStaticMarkup(
      <ChartCard
        title="Fill"
        format="pct"
        labels={["a"]}
        series={[
          { name: "One", values: [92] },
          { name: "Two", values: [81] },
        ]}
        note="Example"
      />,
    );
    expect(html).toContain("92%");
    expect(html).toContain("Two");
    expect(html).toContain("Example");
    expect(
      renderToStaticMarkup(
        <ChartCard
          title="Waste"
          unit="kg"
          labels={["a"]}
          series={[{ name: "W", values: [1200] }]}
        />,
      ),
    ).toContain("1,200 kg");
  });
});

describe("MapPanel", () => {
  it("renders its frame, legend and OpenStreetMap credit before the map library loads", () => {
    const html = renderToStaticMarkup(
      <MapPanel
        label="Route"
        markers={[{ id: "h", kind: "hub", label: "Hub", lng: 73, lat: 19 }]}
        legend={[{ kind: "route", label: "Planned route" }]}
        note="Example"
      />,
    );
    expect(html).toContain('aria-label="Route"');
    expect(html).toContain("Planned route");
    expect(html).toContain("OpenFreeMap");
    expect(html).toContain("Example");
  });
});

describe("PolygonEditor", () => {
  const areas = [
    {
      id: "a",
      label: "Sunrise Towers",
      coordinates: /** @type {[number, number][]} */ ([
        [73.06, 19.03],
        [73.07, 19.03],
        [73.07, 19.04],
      ]),
    },
  ];

  it("offers Draw boundary, Edit points and Undo point, with nothing to undo yet", () => {
    const html = renderToStaticMarkup(<PolygonEditor areas={areas} selected="a" />);
    expect(html).toContain("Draw boundary");
    expect(html).toContain("Edit points");
    expect(html).not.toContain("Move points");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>(?:<svg.*?<\/svg>)?Undo point/);
  });

  it("takes the Undo label from `labels`", () => {
    const html = renderToStaticMarkup(
      <PolygonEditor areas={areas} selected="a" labels={{ undo: "Undo corner" }} />,
    );
    expect(html).toContain("Undo corner");
  });
});
