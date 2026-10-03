import { Button, MoneyText, Skeleton, StatusPill } from "@gharbasket/ui";
import { ArrowRightIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { useWallet } from "@/features/billing/queries.js";
import { shareInvite } from "@/features/community/invite.js";
import { useSociety } from "@/features/community/queries.js";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { shortDate, timeRange, weekdayOf } from "@/features/subscriptions/format.js";
import { useCurrentCycle } from "@/features/subscriptions/queries.js";

/** The brand "parchi with a tick" on kraft paper. */
function DoneMark() {
  return (
    <div
      className="grid size-18 place-items-center rounded-md bg-[var(--gb-kraft-100)]"
      aria-hidden
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="size-14 fill-none stroke-[var(--gb-ink-900)]"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M6 3.5h12v16l-2-1.3-2 1.3-2-1.3-2 1.3-2-1.3-2 1.3z" />
        <path d="M9 8h6M9 11.5h6M9 15h3" />
        <path d="M13.6 15.1l1.1 1.1 2-2.4" className="stroke-[var(--gb-leaf-700)]" />
      </svg>
    </div>
  );
}

/** You're all set: first delivery, address, wallet, and an invite (design Hf15). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/done")({ component: Done });

function Done() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { societyId, towerId, flat, reset } = useOnboardingDraft();
  const { data: cycle } = useCurrentCycle(lang);
  const { data: society } = useSociety(societyId ?? undefined);
  const { data: wallet } = useWallet();
  const tower = society?.towers.find((tw) => tw.id === towerId);

  const invite = () =>
    shareInvite({
      societyId: societyId ?? "",
      text: t("onboarding.done.shareText", { society: society?.name ?? "" }),
      copied: t("onboarding.done.copied"),
    });

  const row = "flex items-baseline justify-between gap-3 border-b border-line py-3";
  return (
    <OnboardingFrame
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          onPress={() => {
            reset();
            navigate({ to: "/week" });
          }}
        >
          {t("onboarding.done.cta")}
        </Button>
      }
    >
      <DoneMark />
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold text-balance">
          {cycle
            ? t("onboarding.done.title", { day: t(`days.long.d${weekdayOf(cycle.delivery_date)}`) })
            : t("onboarding.done.titleSoon")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.done.sub")}</p>
      </div>
      <dl>
        <div className={row}>
          <dt className="text-sm text-fg-secondary">{t("onboarding.done.delivery")}</dt>
          <dd className="font-semibold">
            {cycle ? (
              `${t(`days.short.d${weekdayOf(cycle.delivery_date)}`)} ${shortDate(cycle.delivery_date, lang)} · ${timeRange(cycle.delivery_window, lang)}`
            ) : (
              <Skeleton className="h-5 w-32" />
            )}
          </dd>
        </div>
        <div className={row}>
          <dt className="text-sm text-fg-secondary">{t("onboarding.done.address")}</dt>
          <dd className="font-semibold">
            {[tower ? `${tower.id.toUpperCase()}-${flat}` : flat, society?.name]
              .filter(Boolean)
              .join(", ")}
          </dd>
        </div>
        <div className={row}>
          <dt className="text-sm text-fg-secondary">{t("onboarding.done.wallet")}</dt>
          <dd>
            {wallet ? (
              <MoneyText paise={wallet.balance_paise} />
            ) : (
              <Skeleton className="h-5 w-16" />
            )}
          </dd>
        </div>
      </dl>
      {society && (
        <section className="flex flex-col items-start gap-2.5 rounded-md bg-surface p-4">
          <strong className="text-lg leading-[26px]">{society.name}</strong>
          {society.status === "live" && (
            <StatusPill status="delivered">{t("onboarding.done.free")}</StatusPill>
          )}
          <p className="text-sm leading-[22px] text-fg-secondary">{t("onboarding.done.invite")}</p>
          <Button variant="secondary" onPress={invite}>
            {t("onboarding.done.inviteCta")}
            <ArrowRightIcon aria-hidden size={20} weight="bold" />
          </Button>
        </section>
      )}
    </OnboardingFrame>
  );
}
