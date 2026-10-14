import { Button, formatPaise, SegmentedControl, Skeleton } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { describeItems } from "@/features/community/kindness.js";
import { useImpact, useMyImpact, useSociety } from "@/features/community/queries.js";
import { ShareSheet } from "@/features/community/ShareSheet.jsx";
import { useMyHousehold } from "@/features/identity/profile.js";

/** Our impact: this month for your family or your society, your share, and how it is counted (design HcImpact, M9). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/kindness_/impact")({ component: Impact });

function Impact() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const [scope, setScope] = useState("society");
  const [sharing, setSharing] = useState(false);
  const household = useMyHousehold();
  const society = useSociety(household.data?.society_id);
  const ours = useImpact(household.data?.society_id);
  const mine = useMyImpact();

  const s = society.data;
  const d = scope === "me" ? mine.data : ours.data;
  const month = d
    ? new Intl.DateTimeFormat(lang, { month: "long", timeZone: "UTC" }).format(
        new Date(`${d.period}-15T00:00:00Z`),
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

  const kg = t("impact.kg", { kg: d.food_saved_kg });
  const stats = [
    [kg, t(`impact.stats.${scope}.food`)],
    [String(d.items_donated), t(`impact.stats.${scope}.items`)],
    [formatPaise(d.cooks_earned_paise), t(`impact.stats.${scope}.cooks`)],
    [String("homes" in d ? d.homes : d.homes_invited), t(`impact.stats.${scope}.homes`)],
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
                kg: ours.data?.food_saved_kg ?? 0,
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
