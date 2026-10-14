import {
  Button,
  EmptyState,
  ProgressBar,
  Skeleton,
  StatusPill,
  TiffinSteam,
  toast,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLabel } from "@/features/kitchen/format.js";
import { useAssignments, useKitchenWrite, withBatch } from "@/features/kitchen/queries.js";
import { Muted } from "@/features/kitchen/ui.jsx";

/** Prep: each dish from not started to cooking to packed, offline-safe (design KitchenPrep; M8 `/ready`). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/prep")({ component: KitchenPrep });

/** What each state shows: its pill, and the one step it offers. */
const STEP = /** @type {const} */ ({
  planned: { pill: "planned", next: "preparing", variant: "secondary" },
  preparing: { pill: "preparing", next: "packed", variant: "primary" },
  packed: { pill: "ready", next: null, variant: "quiet" },
});

function KitchenPrep() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const assignments = useAssignments();
  const write = useKitchenWrite();

  if (assignments.isPending) return <Skeleton lines={10} />;
  if (assignments.isError) return <ScreenError onRetry={() => assignments.refetch()} />;
  const a = assignments.data.items.find((x) => x.status !== "declined");
  if (!a) {
    return (
      <>
        <FieldHeader title={t("kitchen.prep.title")} />
        <EmptyState
          illustration={<TiffinSteam />}
          title={t("kitchen.prep.emptyTitle")}
          body={t("kitchen.prep.emptyBody")}
          action={t("kitchen.prep.emptyAction")}
          onAction={() => navigate({ to: "/kitchen" })}
        />
      </>
    );
  }
  const packed = a.batches.filter((b) => b.status === "packed").reduce((n, b) => n + b.portions, 0);

  /** @param {import("@/features/kitchen/queries.js").Batch} b */
  const act = (b) => {
    const next = STEP[b.status].next;
    if (!next) {
      write({
        name: "assignments",
        path: `/v1/assignments/${a.id}/labels`,
        body: { batch_id: b.id },
        update: (d) => d,
      });
      toast(t("kitchen.prep.labelsAgain"));
      return;
    }
    write({
      name: "assignments",
      path: `/v1/assignments/${a.id}/prep`,
      body: { batch_id: b.id, status: next },
      update: (/** @type {import("@/features/kitchen/queries.js").Assignments} */ d) => ({
        ...d,
        items: d.items.map((x) => (x.id === a.id ? withBatch(x, b.id, next) : x)),
      }),
    });
  };

  return (
    <>
      <FieldHeader
        eyebrow={t("kitchen.prep.when", {
          meal: a.meal,
          day: dayLabel(a.date, lang),
          time: a.pack_by,
        })}
        title={t("kitchen.prep.title")}
      />
      <ProgressBar
        tone="accent"
        label={t("kitchen.prep.packed")}
        value={packed}
        max={a.portions}
        valueLabel={t("kitchen.prep.packedValue", { done: packed, total: a.portions })}
      />
      <ul className="flex flex-col gap-2.5">
        {a.batches.map((b) => {
          const step = STEP[b.status];
          return (
            <li
              key={b.id}
              className="flex flex-col gap-2 rounded-md border border-line-strong bg-surface px-3.5 py-3"
            >
              <div className="flex items-center justify-between gap-3">
                <strong className="text-[19px] leading-[26px] font-semibold">
                  {t("kitchen.prep.batch", { dish: b.dish, n: b.portions })}
                </strong>
                <StatusPill status={step.pill}>{t(`kitchen.prep.status.${b.status}`)}</StatusPill>
              </div>
              <Muted>
                {[
                  ...b.societies.map((s) => `${s.name} ${s.portions}`),
                  ...(b.note ? [b.note] : []),
                ].join(" · ")}
              </Muted>
              <Button variant={step.variant} block onPress={() => act(b)}>
                {t(`kitchen.prep.action.${b.status}`)}
              </Button>
            </li>
          );
        })}
      </ul>
      <Muted>{t("kitchen.prep.label")}</Muted>
    </>
  );
}
