import {
  Badge,
  Button,
  EmptyBasket,
  EmptyState,
  formatPaise,
  ProgressBar,
  Sheet,
  Skeleton,
  StatusPill,
} from "@gharbasket/ui";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ProfileActions } from "@/components/ProfileActions.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLabel } from "@/features/logistics/format.js";
import { CodeSheet } from "@/features/packing/CodeSheet.jsx";
import { dayLine } from "@/features/packing/format.js";
import { usePackingDay, useShortages } from "@/features/packing/queries.js";
import { useOpenBag } from "@/features/packing/useOpenBag.js";
import { clockTime, todayIst } from "@/features/subscriptions/format.js";

/** Packing queue: progress, scan a bag to start, bags in route order (designs Packer · Queue, Queue loading, All bags packed; M6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer/")({ component: Queue });

/** Bag status → the design system's process state. */
const PILL = /** @type {const} */ ({ packed: "ready", packing: "preparing", to_pack: "planned" });

function Queue() {
  const day = usePackingDay();
  if (day.isPending) return <QueueLoading />;
  if (!day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const { tasks } = day.data;
  if (tasks.every((x) => x.status === "packed")) return <AllPacked day={day.data} />;
  return <QueueList day={day.data} />;
}

/** @param {{ day: import("@/features/packing/queries.js").PackingDay }} props */
function QueueList({ day }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const [typing, setTyping] = useState(false);
  const { open, error, next } = useOpenBag(day.tasks);
  const packed = day.tasks.filter((x) => x.status === "packed").length;
  const society = Object.fromEntries(day.societies.map((s) => [s.id, s.name]));

  /** "14 lines · ₹690", "15 lines + 2 add-ons", "Tiffin × 2". @param {import("@/features/packing/queries.js").PackingTask} x */
  const what = (x) => {
    if (x.kind === "tiffin") return t("packer.queue.tiffin", { n: x.lines[0]?.qty ?? 1 });
    const addons = x.lines.filter((l) => l.addon).length;
    const n = x.lines.length - addons;
    return addons
      ? t("packer.queue.linesAddons", { n, m: addons })
      : t("packer.queue.lines", { n, amount: formatPaise(x.total_paise) });
  };

  return (
    <div className="flex flex-col gap-2.5">
      <FieldHeader
        eyebrow={dayLine(lang, day)}
        title={t("packer.queue.title")}
        end={<Badge>{t("packer.role")}</Badge>}
      />
      <ProgressBar
        label={t("packer.queue.progress")}
        value={packed}
        max={day.tasks.length}
        valueLabel={t("packer.progressValue", { done: packed, total: day.tasks.length })}
      />
      <ScanField
        label={t("packer.queue.scan")}
        manualLabel={t("packer.queue.manual")}
        expected={next?.bag_qr}
        state={error ? "error" : "idle"}
        errorLabel={error}
        onCode={open}
        onManual={() => setTyping(true)}
      />
      <p className="text-[15px] leading-[22px] text-fg-secondary">{t("packer.queue.order")}</p>
      <ol className="m-0 flex list-none flex-col gap-2.5 p-0">
        {day.tasks.map((x) => (
          <li key={x.id}>
            <Link
              to="/packer/pick"
              search={{ bag: x.bag_qr }}
              className="flex min-h-16 items-center gap-3 rounded-md bg-surface px-3.5 py-3 text-fg no-underline outline-none focus-visible:shadow-focus"
            >
              <span className="grid size-10 flex-none place-items-center rounded-full border-2 border-role-packer bg-bg font-bold tabular-nums">
                {x.route_seq}
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <strong>
                  {x.flat} · {society[x.society_id]}
                </strong>
                <span className="text-[15px] leading-[22px] text-fg-secondary">{what(x)}</span>
              </span>
              <StatusPill status={PILL[x.status]}>
                {t(`packer.queue.status.${x.status}`)}
              </StatusPill>
            </Link>
          </li>
        ))}
      </ol>
      <Account />
      <CodeSheet kind="bag" open={typing} onOpenChange={setTyping} onCode={open} />
    </div>
  );
}

/** Skeleton of the queue (design Packer · Queue loading). */
function QueueLoading() {
  const { t, i18n } = useTranslation();
  return (
    <div aria-busy="true" className="flex flex-col gap-2.5">
      <header className="flex flex-col gap-1">
        <span className="text-[15px] leading-[22px] text-fg-secondary">
          {dayLabel(todayIst(), i18n.resolvedLanguage ?? "en-IN")}
        </span>
        <h1 className="m-0 text-2xl leading-[30px] font-bold">{t("packer.queue.title")}</h1>
      </header>
      <Skeleton className="h-11 w-full" />
      <span className="overflow-hidden rounded-md">
        <Skeleton className="h-16 w-full" />
      </span>
      {[1, 2, 3, 4, 5].map((n) => (
        <div key={n} className="flex items-center gap-3 rounded-md bg-surface px-3.5 py-3">
          <span className="size-10 flex-none overflow-hidden rounded-full">
            <Skeleton className="size-10" />
          </span>
          <Skeleton lines={2} />
        </div>
      ))}
      <p role="status" className="text-[15px] leading-[22px] text-fg-secondary">
        {t("packer.queue.loading")}
      </p>
    </div>
  );
}

/**
 * Every bag packed: crates per society in loading order (design Packer · All bags packed).
 * @param {{ day: import("@/features/packing/queries.js").PackingDay }} props
 */
function AllPacked({ day }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const [showOrder, setShowOrder] = useState(false);
  const shortages = useShortages();
  const total = day.tasks.length;
  const last = day.tasks.reduce(
    (/** @type {string | null} */ at, x) => (x.done_at && (!at || x.done_at > at) ? x.done_at : at),
    null,
  );
  const stops = [...day.societies]
    .sort((a, b) => a.stop - b.stop)
    .map((s) => {
      const bags = day.tasks.filter((x) => x.society_id === s.id);
      const crates = [...new Set(bags.map((x) => x.crate))].map((crate) => ({
        crate,
        bags: bags.filter((x) => x.crate === crate),
      }));
      return { ...s, bags: bags.length, crates };
    })
    .filter((s) => s.bags > 0);
  const names = stops.map((s) => s.name);
  const order =
    names.length > 1
      ? t("packer.done.firstThen", { first: names[0], rest: names.slice(1).join(", ") })
      : (names[0] ?? "");
  const reported = (shortages.data ?? [])
    .filter((i) => i.reported.length > 0)
    .map((i) =>
      t("packer.done.shortItem", {
        item: day.products[i.product_code]?.name ?? i.product_code,
        n: i.reported.length,
      }),
    );
  const rows = [
    ...stops.map((s) => [
      s.name,
      t("packer.done.crates", { crates: s.crates.length, bags: s.bags }),
    ]),
    [t("packer.done.shortages"), reported.length ? reported.join(", ") : t("packer.done.none")],
  ];
  return (
    <div className="flex flex-1 flex-col gap-3">
      <FieldHeader eyebrow={dayLine(lang, day, last)} title={t("packer.queue.title")} />
      <ProgressBar
        label={t("packer.queue.progress")}
        value={total}
        max={total}
        valueLabel={t("packer.progressValue", { done: total, total })}
      />
      <EmptyState
        illustration={<EmptyBasket />}
        title={t("packer.done.title")}
        body={t("packer.done.body", {
          order,
          time: clockTime(day.van_departs_at, lang),
        })}
      />
      <dl className="m-0">
        {rows.map(([k, v]) => (
          <div
            key={k}
            className="flex items-baseline justify-between gap-3 border-b border-line py-2.5"
          >
            <dt className="text-[15px] leading-[22px] text-fg-secondary">{k}</dt>
            <dd className="m-0 text-right font-bold">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-auto pt-2">
        <Button variant="primary" size="lg" block onPress={() => setShowOrder(true)}>
          {t("packer.done.load")}
        </Button>
      </div>
      <Account />
      <Sheet open={showOrder} onOpenChange={setShowOrder} title={t("packer.done.loadTitle")}>
        <ol className="m-0 flex list-none flex-col p-0">
          {stops.flatMap((s) =>
            s.crates.map((c) => (
              <li key={c.crate} className="flex flex-col border-b border-line py-2.5">
                <strong>{t("packer.done.loadRow", { crate: c.crate, society: s.name })}</strong>
                <span className="text-sm leading-5 text-fg-secondary">
                  {t("packer.done.loadBags", {
                    n: c.bags.length,
                    first: c.bags[0].bag_qr,
                    last: c.bags[c.bags.length - 1].bag_qr,
                  })}
                </span>
              </li>
            )),
          )}
        </ol>
      </Sheet>
    </div>
  );
}

/** Language, role switch and sign out, kept quietly under the list (the designs have no spot). */
function Account() {
  const { t } = useTranslation();
  return (
    <section className="mt-6 flex flex-col gap-3 border-t border-line pt-4">
      <h2 className="m-0 text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
        {t("packer.account")}
      </h2>
      <ProfileActions />
    </section>
  );
}
