import {
  Button,
  ErrorState,
  formatPaise,
  ParchiList,
  Skeleton,
  StatusPill,
  SubstituteSheet,
  toast,
} from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { reasonText, shortDate, weekdayOf } from "@/features/subscriptions/format.js";
import {
  useCurrentCycle,
  useCycleAction,
  useSubstitutes,
  useSwapLine,
} from "@/features/subscriptions/queries.js";
import { ApiError } from "@/lib/api.js";
import { markFirstBoxApproved } from "@/lib/install.js";

const FIRST_LINES = 5;
/** @type {Record<string, "planned" | "preparing" | "ready" | "out" | "delivered" | "issue">} */
const PILL = { ready: "preparing", approved: "ready", skipped: "planned", locked: "out" };

/** Your box as a parchi, with Swap on each line while edits are open (designs HcBox, HcSubstitute; M3, M2). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/box")({ component: Box });

function Box() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data: c, isPending, isError, refetch } = useCurrentCycle(lang);
  const approve = useCycleAction("approve");
  const swap = useSwapLine();
  const [all, setAll] = useState(false);
  const [swapping, setSwapping] = useState(
    /** @type {{ id: string, name: string, pricePaise: number } | null} */ (null),
  );
  const subs = useSubstitutes(swapping?.id ?? null, lang);

  if (isPending) return <Skeleton lines={8} />;
  if (isError || !c)
    return (
      <ErrorState
        title={t("onboarding.error")}
        retryLabel={t("onboarding.retry")}
        onRetry={() => refetch()}
      />
    );

  const editable = c.status === "ready";
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
      <ParchiList
        title={t("box.parchi")}
        date={day}
        totalPaise={c.total_paise}
        labels={{
          total: t("box.total", { count: c.lines.length }),
          swap: t("box.swap"),
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
      {c.lines.length > FIRST_LINES && (
        <div>
          <Button variant="quiet" className="-ml-4" onPress={() => setAll((v) => !v)}>
            {all ? t("box.less") : t("box.more", { count: c.lines.length - FIRST_LINES })}
          </Button>
        </div>
      )}
      {c.budget_lock && (
        <p className="text-sm leading-[22px] text-fg-secondary">
          {t(c.budget_lock.spent_paise <= c.budget_lock.budget_paise ? "box.within" : "box.over", {
            budget: formatPaise(c.budget_lock.budget_paise),
          })}
        </p>
      )}
      {editable && (
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
            if (!sub || !from) return;
            swap.mutate(
              { cycle: c, productId: from.id, substituteId: sub.product_id },
              {
                onSuccess: () => toast(t("box.swapped", { from: from.name, to: sub.name })),
                onError,
              },
            );
          }}
        />
      )}
    </div>
  );
}
