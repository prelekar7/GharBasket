import Link from "next/link";
import { linkButton, PublicFooter, PublicHeader } from "@/components/public/Chrome.jsx";
import { SocietyFinder } from "@/components/public/SocietyFinder.jsx";
import { getSocieties } from "@/lib/public-api.js";
import { APP_URL } from "@/lib/public-lang.js";

/**
 * Landing page of Landing.dc.html, at "/" for signed-out visitors (proxy.js still sends a signed-in
 * staff member from "/" to their console home). Static apart from the society list, which is cached.
 */

export const metadata = {
  title: { absolute: "GharBasket · Your kitchen, planned" },
  description:
    "GharBasket plans your family’s week of sabzi, staples and home-cooked tiffins. Approve it by 9 PM; a neighbour in your society hands it over the next morning.",
  alternates: { canonical: "/" },
  robots: { index: true, follow: true },
};

/** @param {string} slot @param {string} name @param {boolean} [tiffin] */
const meal = (slot, name, tiffin = false) => ({ slot, name, tiffin });
const WEEK = [
  {
    day: "Mon",
    date: "5",
    today: false,
    meals: [meal("Lunch", "Varan-bhaat"), meal("Dinner", "Methi thepla")],
  },
  {
    day: "Tue",
    date: "6",
    today: true,
    meals: [meal("Lunch", "Chapati-bhaji", true), meal("Dinner", "Moong dal khichdi")],
  },
  {
    day: "Wed",
    date: "7",
    today: false,
    meals: [meal("Lunch", "Rajma chawal"), meal("Dinner", "Palak paratha")],
  },
  {
    day: "Thu",
    date: "8",
    today: false,
    meals: [meal("Lunch", "Sabudana khichdi"), meal("Dinner", "Fruit and curd")],
  },
];

const STEPS = [
  {
    title: "Tell us about your family",
    body: "Who eats, what you like, food rules and fasting days, and a monthly budget if you want one.",
  },
  {
    title: "Approve your week",
    body: "Every week a plan arrives with a reason for each item. Change anything, skip a week, or just approve by 9 PM the day before.",
  },
  {
    title: "A neighbour hands it over",
    body: "Your society’s Captain receives the crates and brings the bag to your door between 8 and 10 AM.",
  },
];

/** The example week of the design: one katori per meal (green cook, saffron tiffin). Static markup. */
function ExampleWeek() {
  return (
    <ol
      aria-label="An example family week"
      className="grid grid-cols-2 gap-2 min-[480px]:grid-cols-4"
    >
      {WEEK.map((d) => (
        <li
          key={d.date}
          className={`flex flex-col gap-2 rounded-md p-2 ${d.today ? "bg-accent-soft shadow-[inset_0_0_0_2px_var(--color-accent)]" : "bg-bg"}`}
        >
          <div className="flex flex-col">
            <span className="text-sm leading-5 font-semibold">{d.day}</span>
            <span className="flex items-center gap-1 text-xs leading-[18px] text-fg-secondary">
              {d.date}
              {d.today && (
                <span className="rounded-full bg-accent px-1.5 text-xs leading-4 font-bold text-on-accent">
                  Aaj
                </span>
              )}
            </span>
          </div>
          <div
            aria-hidden
            className="grid size-14 grid-cols-2 gap-[3px] self-center rounded-full border-2 border-line-strong p-[7px]"
          >
            {d.meals.map((m) => (
              <span
                key={m.slot}
                className={`aspect-square self-center rounded-full ${m.tiffin ? "bg-accent" : "bg-primary"}`}
              />
            ))}
          </div>
          <ul className="text-xs leading-4">
            {d.meals.map((m) => (
              <li key={m.slot} className="mt-0.5 first:mt-0">
                <span className="sr-only">{m.slot}: </span>
                {m.name}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}

export default async function LandingPage() {
  const societies = await getSocieties("en");
  return (
    <>
      <PublicHeader wide>
        <nav aria-label="Site" className="flex items-center gap-7">
          <span className="hidden items-center gap-7 min-[900px]:flex">
            <a href="#how" className="font-medium text-fg no-underline">
              How it works
            </a>
            <a href="#societies" className="font-medium text-fg no-underline">
              Societies
            </a>
            <Link href="/cook/c7f3a91e" className="font-medium text-fg no-underline">
              Home cooks
            </Link>
            <Link href="/privacy" className="font-medium text-fg no-underline">
              Privacy
            </Link>
          </span>
          <a href={APP_URL} className={`${linkButton.base} ${linkButton.primary}`}>
            Open the app
          </a>
        </nav>
      </PublicHeader>
      <main
        id="content"
        className="flex flex-1 flex-col gap-14 px-4 py-10 text-[17px] leading-[26px] min-[900px]:px-16 min-[900px]:py-14"
      >
        <section className="grid items-center gap-10 min-[900px]:grid-cols-[1.1fr_1fr] min-[900px]:gap-14">
          <div className="flex flex-col gap-5">
            <h1 className="font-display text-[40px] leading-[44px] font-semibold tracking-[-0.01em] min-[900px]:text-[56px] min-[900px]:leading-[60px]">
              Your kitchen, planned.
            </h1>
            <p className="m-0 text-xl leading-[30px]">
              GharBasket plans your family’s week of sabzi, staples and home-cooked tiffins. You
              approve it by 9 PM the evening before; a neighbour in your society hands it over the
              next morning.
            </p>
            <SocietyFinder societies={societies} />
            <p className="m-0 text-base leading-6 text-fg-secondary">
              Kharghar, Kamothe and New Panvel for now. Works on any phone as an app from your
              browser, in English, हिन्दी and मराठी.
            </p>
          </div>
          <div className="rounded-lg bg-surface p-5">
            <ExampleWeek />
          </div>
        </section>

        <section id="how" className="flex scroll-mt-6 flex-col gap-5">
          <h2 className="font-display text-[30px] leading-9 font-semibold">How a week works</h2>
          <ol className="grid gap-4 min-[900px]:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.title} className="flex flex-col gap-2 rounded-md bg-surface p-5">
                <span
                  aria-hidden
                  className="grid size-9 place-items-center rounded-full bg-accent font-bold text-on-accent"
                >
                  {i + 1}
                </span>
                <h3 className="text-[19px] leading-[26px] font-bold">{s.title}</h3>
                <p className="m-0 text-base leading-6 text-fg-secondary">{s.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section
          id="societies"
          className="grid scroll-mt-6 items-start gap-8 min-[900px]:grid-cols-2 min-[900px]:gap-14"
        >
          <div className="flex flex-col gap-3">
            <h2 className="font-display text-[30px] leading-9 font-semibold">
              Delivery starts when your society unlocks
            </h2>
            <p className="m-0 text-base leading-6 text-fg-secondary">
              One van and one Society Captain serve a whole building complex, so delivery is free
              once enough neighbours join. Until then, invite them with your society’s link.
            </p>
          </div>
          <ul>
            {societies.map((s) => (
              <li key={s.slug}>
                <Link
                  href={`/s/${s.slug}`}
                  className="flex items-center justify-between gap-3 border-b border-line py-3.5 text-fg no-underline"
                >
                  <span className="flex flex-col">
                    <strong>{s.name}</strong>
                    <span className="text-sm leading-6 text-fg-secondary">{s.area}</span>
                  </span>
                  <span
                    className={`inline-flex min-h-5 items-center rounded-xs px-1.5 text-xs leading-[18px] font-bold whitespace-nowrap ${s.status === "live" ? "bg-primary text-on-primary" : "bg-accent text-on-accent"}`}
                  >
                    {s.status === "live" ? "Delivering" : "Unlocking"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </main>
      <PublicFooter wide note="GharBasket · a B.Tech major project at SIES GST, Navi Mumbai" />
    </>
  );
}
