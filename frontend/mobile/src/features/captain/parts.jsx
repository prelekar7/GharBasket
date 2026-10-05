import { useTranslation } from "react-i18next";
import { clockTime, istClock, shortDate, weekdayName } from "@/features/subscriptions/format.js";
import { normalizeCode } from "@/lib/scan.js";
import { useDrafts } from "./queries.js";

/** "Tue 6 Oct" / "मंगल 6 अक्टू॰". @param {string} iso @param {string} lang */
export const dayLabel = (iso, lang) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;

/** "8:05 am" / "सुबह 8:05" from an ISO time. @param {string} at @param {string} lang */
export const clock = (at, lang) => clockTime(istClock(at), lang);

/**
 * The van's crates, with the ones scanned on this phone before the handover is sent.
 * @param {import("./day.js").CaptainDay | undefined} day
 */
export function useCrates(day) {
  const [drafts, update] = useDrafts(day?.date);
  const sent = !!day?.stop.handed_over_at;
  const crates = (day?.crates ?? []).map((c) =>
    !sent && drafts.crates.includes(c.id) ? { ...c, state: /** @type {const} */ ("scanned") } : c,
  );
  /** @param {string} raw @returns {{ ok: true, crate: import("./day.js").Crate } | { ok: false, code: string }} */
  const receive = (raw) => {
    const code = normalizeCode(raw);
    const crate = crates.find((c) => c.id === code);
    if (!crate || sent || crate.state === "scanned") return { ok: false, code };
    update((d) => ({ ...d, crates: [...d.crates, code] }));
    return { ok: true, crate };
  };
  const clear = () => update((d) => ({ ...d, crates: [] }));
  return { crates, receive, clear };
}

/** After the handover: when the crates came, and what to do next. @param {{ day: import("./day.js").CaptainDay }} props */
export function ReceivedCard({ day }) {
  const { t, i18n } = useTranslation();
  const at = day.stop.handed_over_at ?? day.stop.eta;
  return (
    <section className="flex flex-col gap-1.5 rounded-md bg-accent-soft p-3.5">
      <h2 className="m-0 text-[19px] leading-[26px] font-bold">
        {t("captain.received.title", { time: clock(at, i18n.resolvedLanguage ?? "en-IN") })}
      </h2>
      <p className="m-0">{t("captain.received.body")}</p>
    </section>
  );
}

/** A bag the captain can still hand over (lobby bags too, if the family is at the door). @param {import("./day.js").Bag} bag */
export const isOpen = (bag) => bag.status === "planned" || bag.status === "in_lobby";

/** Delivered at the door: POST /v1/deliveries/{order_id}/proof. @param {import("./day.js").Bag} bag @param {boolean} typed */
export const proof = (bag, typed) => ({
  path: `/v1/deliveries/${bag.order_id}/proof`,
  body: { proof_kind: "captain_scan", bag_code: bag.code, typed, at: new Date().toISOString() },
});
