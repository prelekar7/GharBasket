import {
  Button,
  Checkbox,
  EmptyState,
  ProgressBar,
  Sheet,
  Skeleton,
  StatusPill,
  TiffinSteam,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLabel } from "@/features/kitchen/format.js";
import {
  CHECKLIST,
  dishes,
  isPacked,
  useKitchenWrite,
  usePrepAssignments,
  withReady,
} from "@/features/kitchen/queries.js";
import { Muted } from "@/features/kitchen/ui.jsx";

/** Prep: each dish from not started to cooking to packed, offline-safe (design KitchenPrep; M8 §3.4 start and ready). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/prep")({ component: KitchenPrep });

/** What each state shows: its pill, and the one step it offers. */
const STEP = /** @type {const} */ ({
  planned: { pill: "planned", variant: "secondary" },
  preparing: { pill: "preparing", variant: "primary" },
  packed: { pill: "ready", variant: "quiet" },
});

function KitchenPrep() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const assignments = usePrepAssignments();
  const write = useKitchenWrite();
  const [packing, setPacking] = useState(/** @type {string | null} */ (null));
  const [ticked, setTicked] = useState(/** @type {string[]} */ ([]));

  if (assignments.isPending) return <Skeleton lines={10} />;
  if (assignments.isError) return <ScreenError onRetry={() => assignments.refetch()} />;
  const a = assignments.data.items.find((x) =>
    ["accepted", "preparing", "ready", "completed"].includes(x.status),
  );
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
  const packed = a.orders.filter(isPacked).reduce((n, o) => n + o.portions, 0);
  const rows = dishes(a);
  const picked = rows.find((d) => d.product_id === packing);

  /** "Start cooking" starts the whole assignment: every dish is then being cooked. */
  const start = () =>
    write({
      name: "assignments",
      path: `/v1/assignments/${a.id}/start`,
      body: { client_at: new Date().toISOString() },
      update: (/** @type {import("@/features/kitchen/queries.js").Assignments} */ d) => ({
        ...d,
        items: d.items.map((x) => (x.id === a.id ? { ...x, status: "preparing" } : x)),
      }),
    });

  /** "Mark packed" for one dish: its orders are ready, with the hygiene checklist ticked. */
  const ready = () => {
    if (!picked) return;
    const ids = picked.orders.filter((o) => !isPacked(o)).map((o) => o.order_id);
    write({
      name: "assignments",
      path: `/v1/assignments/${a.id}/ready`,
      body: {
        order_ids: ids,
        checklist: Object.fromEntries(CHECKLIST.map((k) => [k, true])),
        client_at: new Date().toISOString(),
      },
      update: (/** @type {import("@/features/kitchen/queries.js").Assignments} */ d) => ({
        ...d,
        items: d.items.map((x) => (x.id === a.id ? withReady(x, ids) : x)),
      }),
    });
    setPacking(null);
    setTicked([]);
  };

  return (
    <>
      <FieldHeader
        eyebrow={t("kitchen.prep.when", {
          meal: a.meal,
          day: dayLabel(a.date, lang),
          time: a.ready_by,
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
        {rows.map((d) => {
          const step = STEP[d.status];
          return (
            <li
              key={d.product_id}
              className="flex flex-col gap-2 rounded-md border border-line-strong bg-surface px-3.5 py-3"
            >
              <div className="flex items-center justify-between gap-3">
                <strong className="text-[19px] leading-[26px] font-semibold">
                  {t("kitchen.prep.batch", { dish: d.name, n: d.portions })}
                </strong>
                <StatusPill status={step.pill}>{t(`kitchen.prep.status.${d.status}`)}</StatusPill>
              </div>
              <Muted>
                {d.societies
                  .map(
                    (name) =>
                      `${name} ${d.orders.filter((o) => o.society.name === name).reduce((n, o) => n + o.portions, 0)}`,
                  )
                  .join(" · ")}
              </Muted>
              {d.status === "packed" ? (
                <Muted>
                  {t("kitchen.prep.codes", { codes: d.orders.map((o) => o.short_code).join(", ") })}
                </Muted>
              ) : (
                <Button
                  variant={step.variant}
                  block
                  onPress={() => (d.status === "planned" ? start() : setPacking(d.product_id))}
                >
                  {t(`kitchen.prep.action.${d.status}`)}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
      <Muted>{t("kitchen.prep.label")}</Muted>
      <Sheet
        open={!!picked}
        onOpenChange={(open) => {
          if (!open) {
            setPacking(null);
            setTicked([]);
          }
        }}
        title={t("kitchen.prep.checklistTitle", { dish: picked?.name ?? "" })}
        footer={
          <Button
            variant="primary"
            size="lg"
            block
            isDisabled={ticked.length < CHECKLIST.length}
            onPress={ready}
          >
            {t("kitchen.prep.action.preparing")}
          </Button>
        }
      >
        <div className="flex flex-col gap-1">
          {CHECKLIST.map((k) => (
            <Checkbox
              key={k}
              label={t(`kitchen.prep.checklist.${k}`)}
              isSelected={ticked.includes(k)}
              onChange={(on) => setTicked((c) => (on ? [...c, k] : c.filter((x) => x !== k)))}
            />
          ))}
        </div>
      </Sheet>
    </>
  );
}
