import { useState } from "react";
import { Button } from "./Button.jsx";
import {
  KnowYourCookCard,
  MealCard,
  ParchiList,
  PriceSwapChip,
  SubstituteSheet,
  ThaliWeek,
} from "./Food.jsx";

/** @type {import("@storybook/react-vite").Meta} */
const meta = { title: "Food" };
export default meta;

const week = [
  ["Som", "5 Oct", "Dal chawal", "Methi thepla"],
  ["Mangal", "6 Oct", "Rajma", "Khichdi", true],
  ["Budh", "7 Oct", "Bhindi roti", "Paneer bhurji"],
  ["Guru", "8 Oct", "Poha", null, true],
  ["Shukra", "9 Oct", "Usal", "Pulao"],
  ["Shani", "10 Oct", "Puran poli", "Varan bhaat", true],
  ["Ravi", "11 Oct", "Chole", null],
].map(([day, date, lunch, dinner, tiffin]) => ({
  day: /** @type {string} */ (day),
  date: /** @type {string} */ (date),
  meals: [
    { slot: "Lunch", name: /** @type {string} */ (lunch), tiffin: !!tiffin },
    dinner
      ? { slot: "Dinner", name: /** @type {string} */ (dinner) }
      : { slot: "Dinner", name: "Skip", skip: true },
  ],
}));

export const Thali = { render: () => <ThaliWeek days={week} today={1} /> };

export const Parchi = {
  render: () => (
    <div className="flex flex-col gap-4">
      <ParchiList
        date="Tue 6 Oct"
        onSwap={() => {}}
        items={[
          { name: "Toor dal", qty: "1 kg", pricePaise: 16000, reason: "Dal on 4 days" },
          { name: "Bhindi", qty: "500 g", pricePaise: 4800, swappedFromPaise: 6000 },
          { name: "Tomatoes", qty: "1 kg", pricePaise: 5200, swappedFromPaise: 3600 },
          { name: "Atta", qty: "5 kg", pricePaise: 26000 },
        ]}
        totalPaise={69000}
        after={<p className="py-2 text-sm text-[var(--gb-neel-600)]">+9 more items</p>}
      />
      <PriceSwapChip fromPaise={6000} toPaise={4800} />
    </div>
  ),
};

export const Meals = {
  render: () => (
    <div className="flex flex-col gap-3">
      <MealCard
        slot="Lunch"
        name="Rajma chawal"
        items="Rajma, rice, salad"
        cook="Lata tai"
        reason="Protein for the week"
        pricePaise={9000}
        status="preparing"
        statusLabel="Preparing"
      />
      <MealCard
        slot="रात का खाना"
        name="खिचड़ी"
        items="मूँग दाल, चावल"
        labels={{ cookedBy: "बनाने वाली" }}
        cook="लता ताई"
      />
      <KnowYourCookCard
        name="Lata Shinde"
        kitchen="Lata tai’s kitchen, Kharghar"
        since="2024"
        specialty="My puran poli is my mother's recipe."
        fssai
        hygiene
      />
    </div>
  ),
};

function SheetDemo() {
  const [open, setOpen] = useState(true);
  const [picked, setPicked] = useState("");
  return (
    <div className="flex flex-col gap-2">
      <Button onPress={() => setOpen(true)}>Swap bhindi</Button>
      {picked && <p className="text-sm">Picked: {picked}</p>}
      <SubstituteSheet
        open={open}
        onOpenChange={setOpen}
        item={{ name: "Bhindi", pricePaise: 6000 }}
        onConfirm={(o) => {
          setPicked(o.name);
          setOpen(false);
        }}
        options={[
          { name: "Tondli", qty: "500 g", pricePaise: 4800, reason: "In season" },
          { name: "Gavar", qty: "500 g", pricePaise: 5000 },
          { name: "Dudhi", qty: "1 pc", pricePaise: 4000, reason: "Cheapest this week" },
        ]}
      />
    </div>
  );
}
export const Substitute = { render: () => <SheetDemo /> };
