import { ErrorState, IconButton, Skeleton, SocietyUnlockBar, StatusPill } from "@gharbasket/ui";
import { ArrowLeftIcon, MedalIcon } from "@phosphor-icons/react";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { shareInvite } from "@/features/community/invite.js";
import { useSociety } from "@/features/community/queries.js";

/** Your society: unlock progress, invite, captain, and when the plan starts (design HcSociety, M9). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/society/$societyId")({ component: Society });

function Society() {
  const { t } = useTranslation();
  const router = useRouter();
  const { societyId } = Route.useParams();
  const { data: s, isPending, isError, refetch } = useSociety(societyId);

  if (isPending) return <Skeleton lines={6} />;
  if (isError || !s)
    return (
      <ErrorState
        title={t("onboarding.error")}
        retryLabel={t("onboarding.retry")}
        onRetry={() => refetch()}
      />
    );
  const live = s.status === "live";
  const invite = () =>
    shareInvite({
      societyId: s.id,
      text: t("onboarding.done.shareText", { society: s.name }),
      copied: t("onboarding.done.copied"),
    });

  return (
    <div className="flex flex-col gap-4">
      <header className="-ml-3 flex items-center gap-1">
        <IconButton
          label={t("onboarding.back")}
          variant="quiet"
          onPress={() => router.history.back()}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("society.title")}
        </h1>
      </header>
      <div className="-mt-2 flex items-center justify-between gap-2">
        <p className="text-sm text-fg-secondary">{s.area}</p>
        <StatusPill status={live ? "delivered" : "preparing"}>
          {t(live ? "society.live" : "society.unlocking")}
        </StatusPill>
      </div>
      <SocietyUnlockBar
        society={s.name}
        joined={s.unlock.joined}
        target={s.unlock.threshold}
        captain={s.captain?.name}
        onInvite={invite}
        labels={{
          count: t("onboarding.unlock.count", {
            joined: s.unlock.joined,
            target: s.unlock.threshold,
          }),
          remaining: t("society.remaining", { n: s.unlock.threshold - s.unlock.joined }),
          invite: t("onboarding.unlock.invite"),
          unlocked: t("society.unlocked"),
          started: t("society.started", { captain: s.captain?.name ?? "" }),
          progress: t("society.progress"),
        }}
      />
      <section className="flex gap-3.5 rounded-md bg-surface p-4">
        <div className="grid size-11 shrink-0 place-items-center rounded-full bg-accent-soft text-accent-fg">
          <MedalIcon aria-hidden size={24} />
        </div>
        <div className="flex flex-col gap-0.5">
          <strong>
            {s.captain ? t("society.captainIs", { name: s.captain.name }) : t("society.noCaptain")}
          </strong>
          <p className="text-sm leading-[22px] text-fg-secondary">
            {s.captain
              ? t("onboarding.flat.capBody", { flat: s.captain.flat })
              : t("society.captainBody", { society: s.name })}
          </p>
        </div>
      </section>
      {!live && (
        <section className="flex flex-col gap-1 rounded-md bg-surface p-4">
          <strong>{t("society.planTitle")}</strong>
          <p className="text-sm leading-[22px] text-fg-secondary">{t("society.planBody")}</p>
        </section>
      )}
    </div>
  );
}
