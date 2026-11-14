import { notFound } from "next/navigation";
import { Suspense } from "react";
import { linkButton, PublicFooter, PublicHeader } from "@/components/public/Chrome.jsx";
import { UnlockBar } from "@/components/public/UnlockBar.jsx";
import { getSociety } from "@/lib/public-api.js";
import { APP_URL, fill, pickLang, SITE_URL } from "@/lib/public-lang.js";
import { COPY } from "./copy.js";
import { societyView } from "./view.js";

/**
 * The society invite page of Society.dc.html: server-rendered with the unlock progress and a join
 * button (M9 US-3, M12 US-6). Public and indexable; English or Hindi with `?lang=hi` or the
 * browser's language. The data comes from GET /v1/public/societies/{slug}.
 */

/** @param {{ params: Promise<{ slug: string }> }} props */
export async function generateMetadata({ params }) {
  const { slug } = await params;
  const society = await getSociety(slug, "en");
  if (!society)
    return { title: { absolute: "Society not found · GharBasket" }, robots: { index: false } };
  const v = societyView(society);
  return {
    title: { absolute: `${v.place} · GharBasket` },
    description: v.live
      ? `${v.named} is on GharBasket: planned weekly food, delivered by a neighbour.`
      : v.away === null
        ? `${v.named} is getting ready for free GharBasket delivery. Join your neighbours.`
        : `${v.named} needs ${v.away} more homes to unlock free GharBasket delivery. Join your neighbours.`,
    alternates: {
      canonical: `/s/${slug}`,
      languages: { en: `/s/${slug}`, hi: `/s/${slug}?lang=hi` },
    },
    robots: { index: true, follow: true },
  };
}

function Loading() {
  return (
    <>
      <PublicHeader />
      <div
        aria-hidden
        className="mx-auto flex w-full max-w-[480px] flex-1 flex-col gap-4 px-4 py-5"
      >
        <div className="h-28 animate-pulse rounded-md bg-surface" />
        <div className="h-36 animate-pulse rounded-md bg-surface" />
        <div className="h-48 animate-pulse rounded-md bg-surface" />
      </div>
    </>
  );
}

/** @param {{ params: Promise<{ slug: string }>, searchParams: Promise<{ lang?: string }> }} props */
async function Society({ params, searchParams }) {
  const [{ slug }, query] = await Promise.all([params, searchParams]);
  const lang = await pickLang(query.lang);
  const society = await getSociety(slug, lang);
  if (!society) notFound();

  const t = COPY[lang];
  const v = societyView(society);
  const live = v.live;
  const vars = { name: society.name, n: v.away ?? 0, target: society.unlock_target };
  const title = fill(t[v.titleKey], vars);
  const steps = [
    t.step1,
    live ? t.step2Live : { title: fill(t.step2Unlocking.title, vars), body: t.step2Unlocking.body },
    t.step3,
  ];
  const started = society.captain_name
    ? fill(t.startedWithCaptain, { captain: society.captain_name })
    : t.started;

  return (
    <>
      <PublicHeader>
        <span className="min-w-0 truncate text-[13px] leading-5 text-fg-secondary">
          {SITE_URL.replace(/^https?:\/\//, "")}/s/{slug}
        </span>
      </PublicHeader>
      <main
        id="content"
        lang={lang}
        className="mx-auto flex w-full max-w-[480px] flex-1 flex-col gap-4 px-4 py-5 text-[17px] leading-[26px]"
      >
        <div className="flex flex-col gap-1">
          <span className="text-[15px] leading-[22px] text-fg-secondary">{t.invited}</span>
          <h1 className="font-display text-[30px] leading-9 font-semibold">{title}</h1>
          {society.area && (
            <span className="text-[15px] leading-[22px] text-fg-secondary">{society.area}</span>
          )}
        </div>
        <UnlockBar
          society={society.name}
          joined={v.joined}
          target={society.unlock_target}
          unlocked={live}
          labels={{
            count: t.count,
            early: t.early,
            remaining: t.remaining,
            remainingEarly: t.remainingEarly,
            progress: t.progress,
            invite: t.invite,
            copied: t.copied,
            unlocked: t.unlocked,
            started,
          }}
        />
        <ol>
          {steps.map((s, i) => (
            <li key={s.title} className="flex items-start gap-3 border-b border-line py-2.5">
              <span
                aria-hidden
                className="grid size-7 flex-none place-items-center rounded-full bg-accent text-sm font-bold text-on-accent"
              >
                {i + 1}
              </span>
              <span className="flex flex-col">
                <strong>{s.title}</strong>
                <span className="text-[15px] leading-[22px] text-fg-secondary">{s.body}</span>
              </span>
            </li>
          ))}
        </ol>
        <div className="mt-auto flex flex-col gap-2 pt-2">
          <a
            href={`${APP_URL}/join?s=${encodeURIComponent(slug)}`}
            className={`${linkButton.base} ${linkButton.primary} ${linkButton.lg} w-full`}
          >
            {t.join}
          </a>
          <p className="text-center text-[13px] leading-[22px] text-fg-secondary">{t.fine}</p>
        </div>
      </main>
      <PublicFooter lang={lang} switchLang />
    </>
  );
}

/** @param {{ params: Promise<{ slug: string }>, searchParams: Promise<{ lang?: string }> }} props */
export default function SocietyPage({ params, searchParams }) {
  return (
    <Suspense fallback={<Loading />}>
      <Society params={params} searchParams={searchParams} />
    </Suspense>
  );
}
