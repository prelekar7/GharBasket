import {
  Button,
  formatPaise,
  ParchiList,
  Sheet,
  Skeleton,
  StatusPill,
  SubstituteSheet,
  toast,
} from "@gharbasket/ui";
import { PlusIcon } from "@phosphor-icons/react";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { AddItemSheet } from "@/features/catalog/AddItemSheet.jsx";
import { reasonText, shortDate, weekdayOf } from "@/features/subscriptions/format.js";
import { ProductSheet } from "@/features/subscriptions/ProductSheet.jsx";
import {
  useCurrentCycle,
  useCycleAction,
  useEditLine,
  useSubstitutes,
  useSwapLine,
} from "@/features/subscriptions/queries.js";
import { ApiError } from "@/lib/api.js";
import { markFirstBoxApproved } from "@/lib/install.js";

const FIRST_LINES = 5;
/** @type {Record<string, "planned" | "preparing" | "ready" | "out" | "delivered" | "issue">} */
const PILL = { ready: "preparing", approved: "ready", skipped: "planned", locked: "out" };

/**
 * Your box as a parchi, with Swap on each line while edits are open; tapping a line opens it as a
 * product with swaps and how much (designs HcBox, HcSubstitute, HcProduct; M3, M2).
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/box")({ component: Box });

function Box() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data: c, isPending, isError, refetch } = useCurrentCycle(lang);
  const approve = useCycleAction("approve");
  const swap = useSwapLine();
  const edit = useEditLine();
  const [changing, setChanging] = useState(/** @type {string | null} */ (null));
  const [adding, setAdding] = useState(false);
  const [all, setAll] = useState(false);
  const [swapping, setSwapping] = useState(
    /** @type {{ id: string, name: string, pricePaise: number } | null} */ (null),
  );
  const subs = useSubstitutes(swapping?.id ?? null, lang);

  if (isPending) return <Skeleton lines={8} />;
  if (isError || !c) return <ScreenError onRetry={() => refetch()} />;

  // Swaps stay open after approving, until the cut-off (Core-W3).
  const editable = c.status === "ready" || c.status === "approved";
  const day = `${t(`days.short.d${weekdayOf(c.delivery_date)}`)} ${shortDate(c.delivery_date, lang)}`;
  const shown = all ? c.lines : c.lines.slice(0, FIRST_LINES);
  const byName = new Map(c.lines.map((l) => [l.name, l]));

  /** @param {unknown} e */
  const onError = (e) => {
    const code = e instanceof ApiError ? e.code : "";
    toast(
      t(
        code === "subscriptions.version_conflict"
          ? "week.changed"
          : code === "orders.cutoff_passed"
            ? "box.closed"
            : "onboarding.error",
      ),
    );
    refetch();
  };

  /** Swap a line for a substitute, with Undo on the toast. */
  const swapTo = (
    /** @type {{ id: string, name: string }} */ from,
    /** @type {import("@/features/subscriptions/queries.js").Substitute} */ sub,
  ) =>
    swap.mutate(
      { cycle: c, productId: from.id, substituteId: sub.product_id },
      {
        onSuccess: (after) =>
          toast(t("box.swapped", { from: from.name, to: sub.name }), {
            action: {
              label: t("box.undo"),
              onClick: () =>
                edit.mutate(
                  { cycle: after, productId: sub.product_id, op: "undo_swap" },
                  { onError },
                ),
            },
          }),
        onError,
      },
    );

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-start justify-between gap-2">
        <div className="flex flex-col">
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("box.when", { day })}
          </span>
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            {t("box.title")}
          </h1>
        </div>
        <div className="mt-1.5">
          <StatusPill status={PILL[c.status]}>{t(`week.status.${c.status}`)}</StatusPill>
        </div>
      </header>
      {editable && c.price_alerts?.[0] && (
        <PriceAlert
          alert={c.price_alerts[0]}
          budget={c.budget_lock ? formatPaise(c.budget_lock.budget_paise) : null}
          busy={edit.isPending}
          onKeep={(a) =>
            edit.mutate(
              { cycle: c, productId: a.product_id, op: "undo_swap" },
              { onSuccess: () => toast(t("box.spike.kept", { item: a.item })), onError },
            )
          }
          onOk={(a) =>
            edit.mutate({ cycle: c, productId: a.product_id, op: "dismiss_alert" }, { onError })
          }
        />
      )}
      <ParchiList
        title={t("box.parchi")}
        date={day}
        totalPaise={c.total_paise}
        labels={{
          total: t("box.total", { count: c.lines.length }),
          swap: t("box.swap"),
          change: t("box.qty.change"),
          undo: t("box.undo"),
          swapChip: { up: t("box.up"), saves: t("box.saves") },
        }}
        items={shown.map((l) => ({
          name: l.name,
          qty: l.qty_label,
          pricePaise: l.price_paise,
          reason: l.reason_code
            ? reasonText(t, { code: l.reason_code, params: l.reason_params })
            : undefined,
          swappedFromPaise: l.swapped_from_paise ?? undefined,
        }))}
        onUndo={
          editable
            ? (item) => {
                const line = byName.get(item.name);
                if (line)
                  edit.mutate(
                    { cycle: c, productId: line.product_id, op: "undo_swap" },
                    { onSuccess: () => toast(t("box.undone", { name: line.name })), onError },
                  );
              }
            : undefined
        }
        onItemPress={
          editable
            ? (item) => {
                const line = byName.get(item.name);
                if (line?.step) setChanging(line.product_id);
              }
            : undefined
        }
        onSwap={
          editable
            ? (item) => {
                const line = byName.get(item.name);
                if (line)
                  setSwapping({
                    id: line.product_id,
                    name: line.name,
                    pricePaise: line.price_paise,
                  });
              }
            : undefined
        }
      />
      {editable && (
        <div>
          <Button variant="secondary" onPress={() => setAdding(true)}>
            <PlusIcon aria-hidden size={20} weight="bold" />
            {t("box.add.cta")}
          </Button>
        </div>
      )}
      {/* "+ 9 more items" and the Budget Lock share one row (HcBox). */}
      <div className="flex flex-wrap items-center justify-between gap-x-3">
        {c.lines.length > FIRST_LINES && (
          <Button variant="quiet" className="-ml-4" onPress={() => setAll((v) => !v)}>
            {all ? t("box.less") : t("box.more", { count: c.lines.length - FIRST_LINES })}
          </Button>
        )}
        {c.budget_lock && (
          <p className="text-sm leading-[22px] text-fg-secondary">
            {t(
              c.budget_lock.spent_paise <= c.budget_lock.budget_paise ? "box.within" : "box.over",
              { budget: formatPaise(c.budget_lock.budget_paise) },
            )}
          </p>
        )}
      </div>
      {c.status === "ready" && (
        <Button
          variant="primary"
          size="lg"
          block
          isPending={approve.isPending}
          onPress={() =>
            approve.mutate(c, {
              onSuccess: () => {
                markFirstBoxApproved();
                toast(t("week.approvedToast", { day }));
              },
              onError,
            })
          }
        >
          {t("week.approve")}
        </Button>
      )}
      {swapping && !subs.data && (
        <Sheet
          open
          onOpenChange={(open) => !open && setSwapping(null)}
          title={t("box.sheetTitle", { item: swapping.name })}
        >
          {subs.isError ? <ScreenError onRetry={() => subs.refetch()} /> : <Skeleton lines={6} />}
        </Sheet>
      )}
      {swapping && subs.data && (
        <SubstituteSheet
          open
          onOpenChange={(open) => !open && setSwapping(null)}
          item={{ name: swapping.name, pricePaise: swapping.pricePaise }}
          labels={{
            title: t("box.sheetTitle"),
            note: t("box.sheetNote", {
              item: swapping.name,
              price: formatPaise(swapping.pricePaise),
            }),
            cta: t("box.useThis"),
          }}
          options={subs.data.items.map((s) => ({
            name: s.name,
            qty: s.qty_label,
            pricePaise: s.price_paise,
            reason: s.reason_code
              ? reasonText(t, { code: s.reason_code, params: s.reason_params })
              : undefined,
          }))}
          onConfirm={(picked) => {
            const sub = subs.data.items.find(
              (s) => s.name === picked.name && s.price_paise === picked.pricePaise,
            );
            const from = swapping;
            setSwapping(null);
            if (sub && from) swapTo(from, sub);
          }}
        />
      )}
      <AddItemSheet
        open={adding}
        onClose={() => setAdding(false)}
        inBox={new Set(c.lines.map((l) => l.product_id))}
        busyId={edit.isPending && edit.variables?.op === "add" ? edit.variables.productId : null}
        onAdd={(productId, name) =>
          edit.mutate(
            { cycle: c, productId, op: "add" },
            { onSuccess: () => toast(t("box.add.added", { name })), onError },
          )
        }
      />
      <ProductSheet
        line={c.lines.find((l) => l.product_id === changing) ?? null}
        onClose={() => setChanging(null)}
        busy={edit.isPending || swap.isPending}
        onSwap={(sub) => {
          const line = c.lines.find((l) => l.product_id === changing);
          setChanging(null);
          if (line) swapTo({ id: line.product_id, name: line.name }, sub);
        }}
        onSave={(qty) => {
          const line = c.lines.find((l) => l.product_id === changing);
          if (!line) return;
          edit.mutate(
            { cycle: c, productId: line.product_id, op: "qty", qty },
            {
              onSuccess: () => {
                setChanging(null);
                toast(t("box.qty.saved", { name: line.name }));
              },
              onError,
            },
          );
        }}
        onRemove={() => {
          const line = c.lines.find((l) => l.product_id === changing);
          if (!line) return;
          edit.mutate(
            { cycle: c, productId: line.product_id, op: "remove" },
            {
              onSuccess: (after) => {
                setChanging(null);
                toast(t("box.qty.removed", { name: line.name }), {
                  action: {
                    label: t("box.undo"),
                    onClick: () =>
                      edit.mutate(
                        { cycle: after, productId: line.product_id, op: "restore" },
                        { onError },
                      ),
                  },
                });
              },
              onError,
            },
          );
        }}
      />
    </div>
  );
}

/**
 * "Prices moved this week": what Autopilot swapped to stay within the Budget Lock, with Keep and OK
 * (design Core-B4).
 * @param {{ alert: NonNullable<import("@/features/subscriptions/queries.js").Cycle["price_alerts"]>[number], budget: string | null, busy: boolean, onKeep: (a: any) => void, onOk: (a: any) => void }} props
 */
function PriceAlert({ alert: a, budget, busy, onKeep, onOk }) {
  const { t } = useTranslation();
  return (
    <section
      role="status"
      className="flex flex-col gap-2 rounded-md border-2 border-accent bg-accent-soft px-4 py-3"
    >
      <strong>{t("box.spike.title")}</strong>
      <p className="text-sm leading-5">
        {t(budget ? "box.spike.body" : "box.spike.bodyNoLock", {
          item: a.item,
          pct: a.pct,
          market: a.market,
          budget,
          sub: a.substitute,
          n: a.meals,
        })}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" isDisabled={busy} onPress={() => onKeep(a)}>
          {t("box.spike.keep", { item: a.item, delta: formatPaise(a.keep_delta_paise) })}
        </Button>
        <Button variant="quiet" isDisabled={busy} onPress={() => onOk(a)}>
          {t("box.spike.ok")}
        </Button>
      </div>
    </section>
  );
}
