import { notFound } from "next/navigation";
import { Suspense } from "react";
import { linkButton, PublicFooter, PublicHeader } from "@/components/public/Chrome.jsx";
import { getCook } from "@/lib/public-api.js";
import { APP_URL, pickLang } from "@/lib/public-lang.js";
import { COPY } from "./copy.js";
import { cookView } from "./view.js";

/**
 * Know your cook, from Cook.dc.html, on GET /v1/public/cooks/{handle} (M8 §3.5): her name, her hub,
 * checks, training and dishes, shown only with her consent (the API answers 404 otherwise) and never
 * her area, phone number or address. Not indexed: it is a page about a private person, meant to be
 * opened from the app or the link she shares.
 */

export const metadata = {
  title: { absolute: "Know your cook · GharBasket" },
  robots: { index: false, follow: false },
};

function Loading() {
  return (
    <>
      <PublicHeader />
      <div
        aria-hidden
        className="mx-auto flex w-full max-w-[480px] flex-1 flex-col gap-4 px-4 py-4"
      >
        <div className="h-32 animate-pulse rounded-md bg-surface" />
        <div className="h-40 animate-pulse rounded-md bg-surface" />
        <div className="h-36 animate-pulse rounded-md bg-surface" />
      </div>
    </>
  );
}

/** @param {{ params: Promise<{ handle: string }>, searchParams: Promise<{ lang?: string }> }} props */
async function Cook({ params, searchParams }) {
  const [{ handle }, query] = await Promise.all([params, searchParams]);
  const lang = await pickLang(query.lang);
  const cook = await getCook(handle, lang);
  if (!cook) notFound();

  const t = COPY[lang];
  const v = cookView(cook, lang);
  const pill = (/** @type {string} */ text) => (
    <span
      key={text}
      className="inline-flex items-center gap-1 rounded-full border border-line bg-bg px-2 py-0.5 text-xs leading-[18px] font-semibold"
    >
      <span aria-hidden className="size-2 rounded-full bg-primary" />
      {text}
    </span>
  );

  return (
    <>
      <PublicHeader>
        <span className="text-[13px] leading-5 text-fg-secondary">{t.kind}</span>
      </PublicHeader>
      <main
        id="content"
        lang={lang}
        className="mx-auto flex w-full max-w-[480px] flex-1 flex-col gap-3.5 px-4 py-4 text-[17px] leading-[26px]"
      >
        <article className="flex w-full gap-4 rounded-md bg-surface p-4">
          <div
            aria-hidden
            className="grid size-14 shrink-0 place-items-center rounded-full bg-accent font-display text-xl font-semibold text-on-accent"
          >
            {v.initial}
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
              {t.kind}
            </span>
            <h1 className="text-lg leading-[26px] font-semibold">{cook.display_name}</h1>
            <p className="text-sm leading-[22px] text-fg-secondary">{cook.hub.name}</p>
            {v.skills.length > 0 && (
              <p className="mt-1 font-display italic">{v.skills.join(" · ")}</p>
            )}
            {v.pills.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-2">{v.pills.map(pill)}</div>
            )}
          </div>
        </article>
        <dl>
          {v.facts.map((f) => (
            <div
              key={f.k}
              className="flex justify-between gap-3 border-b border-line py-2.5 text-[15px]"
            >
              <dt className="leading-[22px] text-fg-secondary">{f.k}</dt>
              <dd className="m-0 text-right font-bold">{f.v}</dd>
            </div>
          ))}
        </dl>
        {cook.trainings.length > 0 && (
          <section className="flex flex-col">
            <h2 className="text-lg leading-6 font-bold">{t.trainingsTitle}</h2>
            <ul>
              {cook.trainings.map((m) => (
                <li key={m.code} className="border-b border-line py-2.5">
                  {m.title}
                </li>
              ))}
            </ul>
          </section>
        )}
        {cook.dishes.length > 0 && (
          <section className="flex flex-col">
            <h2 className="text-lg leading-6 font-bold">{t.dishesTitle}</h2>
            <ul>
              {cook.dishes.map((d) => (
                <li key={d.name} className="border-b border-line py-2.5">
                  {d.name}
                </li>
              ))}
            </ul>
          </section>
        )}
        <p className="text-[13px] leading-[22px] text-fg-secondary">{v.privacy}</p>
        <div className="mt-auto pt-2">
          <a
            href={`${APP_URL}/tiffin`}
            className={`${linkButton.base} ${linkButton.primary} ${linkButton.lg} w-full`}
          >
            {t.book}
          </a>
        </div>
      </main>
      <PublicFooter lang={lang} switchLang />
    </>
  );
}

/** @param {{ params: Promise<{ handle: string }>, searchParams: Promise<{ lang?: string }> }} props */
export default function CookPage({ params, searchParams }) {
  return (
    <Suspense fallback={<Loading />}>
      <Cook params={params} searchParams={searchParams} />
    </Suspense>
  );
}
