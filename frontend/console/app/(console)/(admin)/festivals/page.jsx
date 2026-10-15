import { connection } from "next/server";
import { apiGet } from "@/lib/api.js";
import { todayIso } from "@/lib/catalog.js";
import { groupDays } from "@/lib/festivals.js";
import { FestivalsView } from "./FestivalsView.jsx";

export const metadata = { title: "Festival calendar" };

const month = new Intl.DateTimeFormat("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });

/** This month and next, the window the calendar shows. */
function calendarWindow() {
  const [y, m] = todayIso().split("-").map(Number);
  const pad = (/** @type {number} */ n) => String(n).padStart(2, "0");
  const starts = [new Date(Date.UTC(y, m - 1, 1)), new Date(Date.UTC(y, m, 1))];
  const last = new Date(Date.UTC(y, m + 1, 0));
  return {
    from: `${y}-${pad(m)}-01`,
    to: last.toISOString().slice(0, 10),
    months: starts.map((d) => ({
      value: d.toISOString().slice(0, 7),
      label: month.format(d),
    })),
  };
}

export default async function Page() {
  await connection(); // the window depends on today
  const { from, to, months } = calendarWindow();
  // One row per date; no region asks for every region.
  const { items } = await apiGet("/v1/festivals", { searchParams: { from, to } });
  return <FestivalsView festivals={groupDays(items)} months={months} />;
}
