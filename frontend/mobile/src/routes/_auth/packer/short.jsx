import { EmptyState, formatPaise, ShortageReporter, Skeleton, toast } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dayLine } from "@/features/packing/format.js";
import { bagCode } from "@/features/packing/lines.js";
import { usePackingActions, usePackingDay, useShortages } from "@/features/packing/queries.js";

/** @typedef {import("@/features/packing/queries.js").ShortBagOut} ShortBagOut */

/** Report an item that came in short; each family's own choice decides a swap or money back (design Packer · Shortages; M6 US-5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer/short")({ component: Shortages });

/** The bags a shortage of `short` units falls on, in the server's order. @param {ShortBagOut[]} bags @param {number} short */
function fallsOn(bags, short) {
  const out = [];
  let left = short;
  for (const b of bags) {
    if (left <= 0) break;
    out.push(b);
    left -= b.qty;
  }
  return out;
}

function Shortages() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const day = usePackingDay();
  const shortages = useShortages();
  const { reportShortage } = usePackingActions();
  const [value, setValue] = useState(/** @type {number | null} */ (null));

  if (day.isPending || shortages.isPending) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3">
        <Skeleton className="h-4 w-44" />
        <Skeleton className="h-16 w-full" />
        <Skeleton lines={6} />
      </div>
    );
  }
  if (!day.data || !shortages.data) {
    return (
      <ScreenError
        onRetry={() => {
          day.refetch();
          shortages.refetch();
        }}
      />
    );
  }
  // The first goods-receipt gap not reported yet; one at a time.
  const item = shortages.data.find(
    (i) => i.needed != null && i.received != null && i.reported.length === 0 && i.bags.length,
  );
  const reported = shortages.data.filter((i) => i.reported.length > 0);

  let reporter = null;
  if (item?.needed != null && item.received != null) {
    // Counted in steps: "bunch" for a step of one, else "× 250 g".
    const unit = item.step.qty === 1 ? t(`packer.unit.${item.step.unit}`) : `× ${item.step.label}`;
    const short = value ?? Math.max(0, item.needed - item.received);
    const bags = fallsOn(item.bags, short);
    const v = { item: item.name, unit, needed: item.needed, received: item.received };
    reporter = (
      <ShortageReporter
        item={v.item}
        unit={unit}
        needed={item.needed}
        received={item.received}
        value={short}
        onChange={setValue}
        bags={bags.map((b) => ({
          bag: `${bagCode(b)} · ${b.society}`,
          flat: b.flat_label ?? "",
          outcome: b.outcome ?? "refund",
          detail:
            b.outcome === "swap"
              ? t("packer.short.swapDetail", { item: b.substitute?.name })
              : t("packer.short.refundDetail", { amount: formatPaise(b.refund_paise ?? 0) }),
        }))}
        onApply={() => {
          if (!bags.length) return;
          reportShortage(item, bags);
          setValue(null);
          toast(t("packer.short.applied", { item: v.item, n: bags.length }));
        }}
        labels={{
          title: t("packer.short.reporter.title", v),
          gap: t("packer.short.reporter.gap", v),
          howShort: t("packer.short.reporter.howShort", v),
          who: t("packer.short.reporter.who"),
          swap: t("packer.short.reporter.swap"),
          refund: t("packer.short.reporter.refund"),
          apply: t("packer.short.reporter.apply", { n: bags.length }),
          applyOne: t("packer.short.reporter.apply", { n: 1 }),
          rule: t("packer.short.reporter.rule"),
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <FieldHeader eyebrow={dayLine(lang, day.data)} title={t("packer.short.title")} />
      {reporter ?? (
        <EmptyState title={t("packer.short.noneTitle")} body={t("packer.short.noneBody")} />
      )}
      {reported.length > 0 && (
        <section className="flex flex-col">
          <h2 className="m-0 text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("packer.short.reported")}
          </h2>
          <ul className="m-0 list-none p-0">
            {reported.map((i) => {
              const swaps = i.reported.filter((b) => b.outcome === "swap").length;
              const refunds = i.reported.filter((b) => b.outcome === "refund").length;
              return (
                <li
                  key={i.product_id}
                  className="flex items-baseline justify-between gap-3 border-b border-line py-2.5"
                >
                  <strong>
                    {t("packer.done.shortItem", { item: i.name, n: i.reported.length })}
                  </strong>
                  <span className="text-right text-sm leading-5 text-fg-secondary">
                    {t("packer.short.outcomes", { swaps, refunds })}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
