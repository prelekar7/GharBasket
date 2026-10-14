import {
  Badge,
  Button,
  EmptyBasket,
  EmptyState,
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
import { bagCode } from "@/features/packing/lines.js";
import { usePackingDay, usePrefetchNext, useShortages } from "@/features/packing/queries.js";
import { useOpenBag } from "@/features/packing/useOpenBag.js";
import { clockTime, todayIst } from "@/features/subscriptions/format.js";

/** Packing queue: progress, scan a bag to start, bags in route order (designs Packer · Queue, Queue loading, All bags packed; M6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer/")({ component: Queue });

/** Bag status → the design system's process state. */
const PILL = /** @type {const} */ ({ packed: "ready", picking: "preparing", queued: "planned" });

function Queue() {
  const day = usePackingDay();
  if (day.isPending) return <QueueLoading />;
  if (!day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const { tasks } = day.data;
  if (tasks.length === 0) return <NoBags day={day.data} />;
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
  usePrefetchNext(day.tasks);

  /** "14 lines" for a box, "2 lines · add-on order" for an add-on. @param {import("@/features/packing/queries.js").PackingTask} x */
  const what = (x) =>
    t(x.order_kind === "addon" ? "packer.queue.addon" : "packer.queue.lines", { n: x.lines_total });

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
                {x.seq}
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <strong>{[x.flat_label, x.society.name].filter(Boolean).join(" · ")}</strong>
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

/**
 * Nothing built yet for today: the queue is made at 05:00.
 * @param {{ day: import("@/features/packing/queries.js").PackingDay }} props
 */
function NoBags({ day }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="flex flex-col gap-3">
      <FieldHeader
        eyebrow={dayLine(i18n.resolvedLanguage ?? "en-IN", day)}
        title={t("packer.queue.title")}
      />
      <EmptyState title={t("packer.queue.emptyTitle")} body={t("packer.queue.emptyBody")} />
      <Account />
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
    (/** @type {string | null} */ at, x) =>
      x.packed_at && (!at || x.packed_at > at) ? x.packed_at : at,
    null,
  );
  // Societies in the order of their first bag; a bag closed a moment ago has no crate until it is sent.
  const stops = [...new Map(day.tasks.map((x) => [x.society.id, x.society])).values()].map((s) => {
    const bags = day.tasks.filter((x) => x.society.id === s.id);
    const crates = [...new Set(bags.map((x) => x.crate_no))]
      .sort((a, b) => (a ?? 99) - (b ?? 99))
      .map((crate) => ({ crate: crate ?? "–", bags: bags.filter((x) => x.crate_no === crate) }));
    return { ...s, bags: bags.length, crates };
  });
  const names = stops.map((s) => s.name);
  const order =
    names.length > 1
      ? t("packer.done.firstThen", { first: names[0], rest: names.slice(1).join(", ") })
      : (names[0] ?? "");
  const reported = (shortages.data ?? [])
    .filter((i) => i.reported.length > 0)
    .map((i) =>
      t("packer.done.shortItem", {
        item: i.name,
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
                    first: bagCode(c.bags[0]),
                    last: bagCode(c.bags[c.bags.length - 1]),
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
