import { Button, Dialog, EmptyState, RadioCards, Skeleton, TiffinSteam } from "@gharbasket/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { clock, dateLabel, dayLabel } from "@/features/kitchen/format.js";
import {
  dishes,
  useAssignments,
  useKitchenWrite,
  usePartnerMe,
} from "@/features/kitchen/queries.js";
import { Muted, Row } from "@/features/kitchen/ui.jsx";
import { useTrainings } from "@/features/partners/queries.js";
import { istClock } from "@/features/subscriptions/format.js";

/** Tomorrow's tiffins: the counts, accept or decline, or a quiet day (designs KitchenToday, KitchenNoTiffins; M8 US-6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/")({ component: KitchenToday });

function KitchenToday() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const me = usePartnerMe();
  const assignments = useAssignments();
  const trainings = useTrainings();
  const write = useKitchenWrite();
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("other");

  if (me.isPending || assignments.isPending) return <Skeleton lines={8} />;
  if (me.isError || assignments.isError) {
    return (
      <ScreenError
        onRetry={() => {
          me.refetch();
          assignments.refetch();
        }}
      />
    );
  }
  const header = (
    <FieldHeader
      eyebrow={t("kitchen.hello", { name: me.data.display_name, hub: me.data.hub_name })}
      title={t("kitchen.today.title")}
    />
  );
  const a = assignments.data.items[0];
  if (!a) {
    return (
      <>
        {header}
        <div className="flex flex-col gap-4 pt-3">
          <EmptyState
            illustration={<TiffinSteam />}
            title={t("kitchen.today.emptyTitle", { day: dayLabel(assignments.data.date, lang) })}
            body={t("kitchen.today.emptyBody")}
            action={t("kitchen.today.emptyAction")}
            onAction={() => navigate({ to: "/kitchen/menu" })}
          />
          <Muted>{t("kitchen.today.emptyNote")}</Muted>
        </div>
      </>
    );
  }

  const due = trainings.data?.modules.find((m) => !m.result?.passed && m.due);
  const respond = (/** @type {"accept" | "decline"} */ action) =>
    write({
      name: "assignments",
      path: `/v1/assignments/${a.id}/${action}`,
      body: action === "decline" ? { reason } : undefined,
      update: (/** @type {import("@/features/kitchen/queries.js").Assignments} */ d) => ({
        ...d,
        items: d.items.map((x) =>
          x.id === a.id ? { ...x, status: action === "accept" ? "accepted" : "declined" } : x,
        ),
      }),
    });
  const day = dayLabel(a.date, lang);
  const accepted = ["accepted", "preparing", "ready", "completed"].includes(a.status);

  return (
    <>
      {header}
      <section className="flex flex-col gap-1.5 rounded-md bg-accent-soft p-4">
        <Muted>{t("kitchen.today.forDay", { meal: a.meal, day })}</Muted>
        <span className="text-[32px] leading-9 font-bold tabular-nums">
          {t("kitchen.tiffins", { n: a.portions })}
        </span>
        <p>
          {a.status === "offered"
            ? t("kitchen.today.window", { time: clock(istClock(a.respond_by), lang) })
            : accepted
              ? t("kitchen.today.accepted")
              : a.status === "declined"
                ? t("kitchen.today.declined", { day })
                : t("kitchen.today.closed", { day })}
        </p>
      </section>
      <ul>
        {dishes(a).map((d) => (
          <Row
            key={d.product_id}
            title={d.name}
            sub={d.societies.join(" · ")}
            end={<strong className="tabular-nums">{d.portions}</strong>}
          />
        ))}
      </ul>
      <Muted>{t("kitchen.today.fair")}</Muted>
      {due?.due && (
        <Link
          to="/kitchen/training"
          className="text-[15px] leading-[22px] font-semibold text-link underline"
        >
          {t("kitchen.today.training", { minutes: due.minutes, date: dateLabel(due.due, lang) })}
        </Link>
      )}
      <div className="mt-auto flex flex-col gap-1 pt-2">
        {a.status === "offered" && (
          <>
            <Button variant="primary" size="lg" block onPress={() => respond("accept")}>
              {t("kitchen.today.accept", { n: a.portions })}
            </Button>
            <Button variant="quiet" block onPress={() => setDeclining(true)}>
              {t("kitchen.today.decline")}
            </Button>
          </>
        )}
        {accepted && (
          <Button
            variant="primary"
            size="lg"
            block
            onPress={() => navigate({ to: "/kitchen/prep" })}
          >
            {t("kitchen.today.toPrep")}
          </Button>
        )}
      </div>
      <Dialog
        isOpen={declining}
        onOpenChange={setDeclining}
        tone="danger"
        title={t("kitchen.today.declineTitle", { day })}
        confirmLabel={t("kitchen.today.declineConfirm")}
        cancelLabel={t("kitchen.today.declineCancel")}
        onConfirm={() => respond("decline")}
      >
        <p>{t("kitchen.today.declineBody", { n: a.portions })}</p>
        <RadioCards
          label={t("kitchen.today.declineWhy")}
          value={reason}
          onChange={setReason}
          options={["unwell", "no_ingredients", "family", "other"].map((value) => ({
            value,
            title: t(`kitchen.today.reason.${value}`),
          }))}
        />
      </Dialog>
    </>
  );
}
