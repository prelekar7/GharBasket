import { Button, formatPaise, SegmentedControl, Skeleton } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { describeItems } from "@/features/community/kindness.js";
import { useImpact, useMyImpact, useSociety } from "@/features/community/queries.js";
import { ShareSheet } from "@/features/community/ShareSheet.jsx";
import { useMyHousehold } from "@/features/identity/profile.js";
import { todayIst } from "@/features/subscriptions/format.js";

/** Our impact: your family so far, or your society this month, your share, and how it is counted (design HcImpact, M9). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/kindness_/impact")({ component: Impact });

function Impact() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const [scope, setScope] = useState("society");
  const [sharing, setSharing] = useState(false);
  const household = useMyHousehold();
  const society = useSociety(household.data?.society_id);
  const ours = useImpact(household.data?.society_id, todayIst().slice(0, 7));
  const mine = useMyImpact();

  const s = society.data;
  const d = scope === "me" ? mine.data : ours.data;
  const month =
    scope === "me"
      ? t("impact.allTime")
      : ours.data
        ? new Intl.DateTimeFormat(lang, { month: "long", timeZone: "UTC" }).format(
            new Date(`${ours.data.period}-15T00:00:00Z`),
          )
        : "";
  const header = (
    <header className="flex flex-col">
      <span className="min-h-[18px] text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
        {[s?.name, month].filter(Boolean).join(" · ")}
      </span>
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("impact.title")}</h1>
    </header>
  );
  const pending = household.isPending || mine.isPending || (scope === "society" && ours.isPending);
  if (pending)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  if (!d || household.isError || (scope === "me" ? mine.isError : ours.isError))
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError
          onRetry={() => {
            household.refetch();
            mine.refetch();
            ours.refetch();
          }}
        />
      </div>
    );

  // A society's numbers are null until 5 homes have joined in (M9 §3.7): a dash, never a zero.
  const n = (/** @type {number | null} */ v) => (v == null ? "—" : String(v));
  const stats =
    d.scope === "me"
      ? [
          [String(d.meals_donated), t("impact.stats.me.meals")],
          [String(d.items_donated), t("impact.stats.me.items")],
          [t("impact.kg", { kg: d.kg_donated }), t("impact.stats.me.kg")],
          [String(d.homes_invited), t("impact.stats.me.homes")],
        ]
      : [
          [t("impact.kg", { kg: d.hub.food_saved_kg }), t("impact.stats.society.food")],
          [n(d.items_donated), t("impact.stats.society.items")],
          [
            d.hub.partners_earned_paise == null ? "—" : formatPaise(d.hub.partners_earned_paise),
            t("impact.stats.society.cooks"),
          ],
          [n(d.homes_joined), t("impact.stats.society.homes")],
        ];
  const you = mine.data;

  return (
    <div className="flex min-h-[calc(100dvh-160px)] flex-col gap-3">
      {header}
      <div>
        <SegmentedControl
          label={t("impact.scope")}
          value={scope}
          onChange={setScope}
          options={[
            { value: "me", label: t("impact.me") },
            { value: "society", label: t("impact.society") },
          ]}
        />
      </div>
      <dl className="grid grid-cols-2 gap-2.5">
        {stats.map(([value, label]) => (
          <div key={label} className="flex flex-col gap-0.5 rounded-md bg-surface px-4 py-3.5">
            <dt className="order-2 text-sm leading-5 text-fg-secondary">{label}</dt>
            <dd className="order-1 font-display text-[32px] leading-[38px] font-semibold tabular-nums">
              {value}
            </dd>
          </div>
        ))}
      </dl>
      {d.scope === "society" && d.contributors === "under_5" && (
        <p className="text-sm leading-5 text-fg-secondary">{t("impact.underFive")}</p>
      )}
      {you && (
        <section className="flex flex-col gap-1.5 rounded-md bg-accent-soft px-4 py-3.5">
          <strong>{t("impact.youTitle")}</strong>
          <p className="text-[15px] leading-[22px]">
            {you.donated.length
              ? t("impact.you", {
                  items: describeItems(t, lang, you.donated, false),
                  points: you.points_earned,
                })
              : t("impact.youNone")}
          </p>
        </section>
      )}
      <p className="text-sm leading-5 text-fg-secondary">{t("impact.how")}</p>
      {s && (
        <div className="mt-auto">
          <Button block onPress={() => setSharing(true)}>
            {t("impact.share")}
          </Button>
          <ShareSheet
            open={sharing}
            onClose={() => setSharing(false)}
            society={s}
            message={(link) =>
              t("impact.shareMessage", {
                society: s.name,
                kg: ours.data?.hub.food_saved_kg ?? 0,
                n: ours.data?.items_donated ?? 0,
                link,
              })
            }
          />
        </div>
      )}
    </div>
  );
}
