import { useState } from "react";
import { Chip, QtyStepper, RadioCards, SegmentedControl, Select, Tabs } from "./Choice.jsx";
import { MoneyText } from "./Money.jsx";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Money and choices" };
export default meta;

export const Money = {
  render: () => (
    <div className="flex flex-col gap-3">
      <MoneyText paise={131000} size="hero" />
      <MoneyText paise={69000} size="lg" />
      <span className="flex items-baseline gap-2">
        <MoneyText paise={4800} />
        <MoneyText paise={6000} size="sm" strike />
      </span>
      <span className="flex gap-3">
        <MoneyText paise={3000} delta />
        <MoneyText paise={-2000} delta />
      </span>
    </div>
  ),
};

function ChipsDemo() {
  const [veg, setVeg] = useState(true);
  const [jain, setJain] = useState(false);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Chip isSelected={veg} onChange={setVeg}>
          Veg
        </Chip>
        <Chip isSelected={jain} onChange={setJain}>
          Jain
        </Chip>
        <Chip>उपवास</Chip>
      </div>
      <div>
        <Chip kind="reason">Ekadashi tomorrow</Chip>
      </div>
    </div>
  );
}
export const Chips = { render: () => <ChipsDemo /> };

function StepperDemo() {
  const [kg, setKg] = useState(2);
  const [n, setN] = useState(1);
  return (
    <div className="flex flex-col gap-3">
      <QtyStepper label="Onions" unit="kg" value={kg} onChange={setKg} />
      <QtyStepper
        label="दूध"
        unit="लीटर"
        value={n}
        onChange={setN}
        labels={{ decrease: "कम करें", increase: "बढ़ाएँ" }}
      />
    </div>
  );
}
export const Stepper = { render: () => <StepperDemo /> };

function ChoicesDemo() {
  const [week, setWeek] = useState("this");
  const [plan, setPlan] = useState("family");
  const [tab, setTab] = useState("upcoming");
  const [tower, setTower] = useState("");
  return (
    <div className="flex flex-col gap-5">
      <SegmentedControl
        label="Week"
        value={week}
        onChange={setWeek}
        options={[
          { value: "this", label: "This week" },
          { value: "next", label: "Next week" },
        ]}
      />
      <Tabs
        label="Orders"
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "upcoming", label: "Upcoming", count: 2 },
          { value: "past", label: "Past" },
        ]}
      >
        <p className="text-sm text-fg-secondary">
          {tab === "upcoming" ? "Tue 6 Oct · Box of 14 items" : "No past orders yet"}
        </p>
      </Tabs>
      <RadioCards
        label="Plan size"
        value={plan}
        onChange={setPlan}
        options={[
          {
            value: "small",
            title: "For 2",
            description: "Couple or small home",
            meta: <MoneyText paise={45000} size="sm" />,
          },
          {
            value: "family",
            title: "For 4",
            description: "Most families",
            badge: "Recommended",
            meta: <MoneyText paise={69000} size="sm" />,
          },
          {
            value: "large",
            title: "For 6+",
            description: "Joint family",
            meta: <MoneyText paise={98000} size="sm" />,
          },
        ]}
      />
      <Select
        label="Tower"
        placeholder="Choose your tower"
        value={tower}
        onChange={setTower}
        options={[
          { value: "a", label: "A wing" },
          { value: "b", label: "B wing" },
        ]}
      />
    </div>
  );
}
export const Choices = { render: () => <ChoicesDemo /> };
