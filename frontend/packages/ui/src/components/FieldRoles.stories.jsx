import { useState } from "react";
import {
  BagLabel,
  CrateCounter,
  FlatChecklist,
  RouteStopCard,
  ShortageReporter,
} from "./FieldRoles.jsx";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Field roles" };
export default meta;

export const Bag = {
  render: () => (
    <div className="flex flex-col gap-3">
      <BagLabel
        code="B-214"
        flat="B-402"
        society="Sunrise Towers"
        stop={1}
        crate="S-1"
        lines={12}
      />
      <BagLabel
        code="K-105"
        flat="A-101"
        society="कृष्णा कुंज"
        stop={2}
        crate="K-1"
        lines={2}
        flags={["tiffin", "fragile"]}
        labels={{
          bag: "बैग",
          flat: "फ़्लैट",
          stop: "स्टॉप {n}",
          crate: "क्रेट {crate}",
          lines: "{n} चीज़ें",
          flags: { tiffin: "टिफ़िन", fragile: "नाज़ुक" },
        }}
      />
    </div>
  ),
};

export const Crates = {
  render: () => (
    <CrateCounter
      crates={[
        { id: "S-1", state: "scanned", bags: 16 },
        { id: "S-2", state: "scanned", bags: 15 },
        { id: "S-3", state: "waiting", bags: 15 },
        { id: "K-1", state: "missing", bags: 12 },
      ]}
    />
  ),
};

export const Stops = {
  render: () => (
    <div className="flex flex-col gap-3">
      <RouteStopCard
        n={1}
        society="Sunrise Towers"
        where="Gate 2 · Sector 20, Kharghar"
        captain="Meena tai"
        window="8:05"
        crates={3}
        bags={46}
        status="done"
      />
      <RouteStopCard
        n={2}
        society="Krishna Kunj"
        where="Sector 17, New Panvel"
        captain="Meena tai"
        window="8:40"
        crates={1}
        bags={12}
        status="current"
      />
      <RouteStopCard n={3} society="Hub" crates={4} status="issue" />
    </div>
  ),
};

export const Flats = {
  render: () => (
    <FlatChecklist
      onSelect={() => {}}
      items={[
        { flat: "A-1203", note: "Box + tiffin", status: "delivered", statusLabel: "Delivered" },
        { flat: "B-402", note: "Box", status: "out", statusLabel: "Next" },
        {
          flat: "C-807",
          note: "Box · not home, leave with guard",
          status: "ready",
          statusLabel: "To deliver",
        },
        {
          flat: "A-604",
          note: "Methi short, swapped to palak",
          status: "issue",
          statusLabel: "Problem",
        },
      ]}
    />
  ),
};

function ShortageDemo() {
  const [short, setShort] = useState(4);
  return (
    <ShortageReporter
      item="Methi"
      unit="judi"
      needed={30}
      received={26}
      value={short}
      onChange={setShort}
      bags={[
        {
          bag: "B-214 · Sunrise Towers",
          flat: "B-402",
          outcome: "swap",
          detail: "Palak, allowed by the family",
        },
        {
          bag: "B-216 · Sunrise Towers",
          flat: "A-604",
          outcome: "refund",
          detail: "₹20 to the wallet",
        },
      ]}
    />
  );
}

export const Shortage = { render: () => <ShortageDemo /> };
