import {
  Badge,
  Button,
  IconButton,
  MoneyText,
  Skeleton,
  SocietyUnlockBar,
  StatusPill,
  toast,
} from "@gharbasket/ui";
import { ArrowLeftIcon, CopyIcon, MedalIcon } from "@phosphor-icons/react";
import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { deliveryText, nextDeliveryDate } from "@/features/community/delivery.js";
import { inviteLink } from "@/features/community/invite.js";
import {
  sawUnlocking,
  useDeliveryDays,
  useImpact,
  useRecordInvite,
  useSociety,
  useSocietyUnlock,
} from "@/features/community/queries.js";
import { ShareSheet } from "@/features/community/ShareSheet.jsx";
import { UnlockCelebration } from "@/features/community/UnlockCelebration.jsx";
import { shortDate, weekdayName } from "@/features/subscriptions/format.js";

/**
 * Your society: unlock progress or, once live, delivery days, the captain, the invite link and
 * this month's impact (designs HcSociety, Core-S1); the invite sheet (S3) and the one-time unlock
 * celebration (S4). M9.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/society/$societyId")({ component: Society });

const seenKey = (/** @type {string} */ id) => `gb-unlock-seen-${id}`;

function Society() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const navigate = useNavigate();
  const { societyId } = Route.useParams();
  const { data: s, isPending, isError, refetch } = useSociety(societyId);
  const unlockQuery = useSocietyUnlock(s?.id);
  const deliveryQuery = useDeliveryDays(s?.id);
  const impact = useImpact(s?.status === "live" ? s.id : undefined);
  const record = useRecordInvite();
  const [sharing, setSharing] = useState(false);
  const [seen, setSeen] = useState(() => {
    try {
      return localStorage.getItem(seenKey(societyId)) === "1";
    } catch {
      return true;
    }
  });

  if (isPending || unlockQuery.isPending || deliveryQuery.isPending) return <Skeleton lines={6} />;
  if (isError || !s) return <ScreenError onRetry={() => refetch()} />;
  const live = s.status === "live";
  const unlock = unlockQuery.data ?? null;
  const delivery = deliveryQuery.data ?? [];
  const days = deliveryText(delivery, lang);
  const firstDate = nextDeliveryDate(delivery);
  const link = inviteLink(s.slug);
  // The API has no `unlocked_at`: celebrate once, on the visit after this phone saw it unlocking.
  const justUnlocked = live && !seen && !!unlock && sawUnlocking(s.id);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(link);
      toast(t("onboarding.done.copied"));
    } catch {
      toast(t("society.share.copyFailed"));
    }
    record.mutate({ societyId: s.id, channel: "copy" });
  };

  if (justUnlocked)
    return (
      <UnlockCelebration
        society={s.name}
        homes={unlock?.joined ?? 0}
        start={firstDate ? `${weekdayName(firstDate, lang)} ${shortDate(firstDate, lang)}` : ""}
        days={days ?? ""}
        captain={s.captain?.display_name ?? null}
        onDone={() => {
          try {
            localStorage.setItem(seenKey(s.id), "1");
          } catch {
            // Without storage it may show again; harmless.
          }
          setSeen(true);
          navigate({ to: "/week" });
        }}
      />
    );

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
      <div className="-mt-2 flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          {live ? (
            <h2 className="text-lg leading-[26px] font-bold">{s.name}</h2>
          ) : (
            <p className="text-sm text-fg-secondary">{s.area}</p>
          )}
          {live ? (
            <Badge tone="primary">{t("society.live")}</Badge>
          ) : (
            <StatusPill status="preparing">{t("society.unlocking")}</StatusPill>
          )}
        </div>
        {live && days && (
          <p className="text-sm leading-5 text-fg-secondary">
            {t("society.delivery", { area: s.area ?? s.name, days })}
          </p>
        )}
      </div>
      {!live && unlock && (
        <SocietyUnlockBar
          society={s.name}
          joined={unlock.joined}
          target={unlock.threshold}
          captain={s.captain?.display_name}
          onInvite={() => setSharing(true)}
          labels={{
            count: t("onboarding.unlock.count", {
              joined: unlock.joined,
              target: unlock.threshold,
            }),
            remaining: t("society.remaining", { n: unlock.threshold - unlock.joined }),
            invite: t("onboarding.unlock.invite"),
            unlocked: t("society.unlocked"),
            started: t("society.started", { captain: s.captain?.display_name ?? "" }),
            progress: t("society.progress"),
          }}
        />
      )}
      <section className="flex gap-3.5 rounded-md bg-surface p-4">
        <div className="grid size-11 shrink-0 place-items-center rounded-full bg-accent-soft font-semibold text-accent-fg">
          {s.captain ? initials(s.captain.display_name) : <MedalIcon aria-hidden size={24} />}
        </div>
        <div className="flex flex-col gap-0.5">
          <strong>
            {s.captain
              ? t("society.captainIs", { name: s.captain.display_name })
              : t("society.noCaptain")}
          </strong>
          <p className="text-sm leading-5 text-fg-secondary">
            {s.captain ? t("society.captainDoes") : t("society.captainBody", { society: s.name })}
          </p>
          {!s.captain && (
            <div>
              <Button
                variant="quiet"
                className="-ml-4"
                onPress={() => navigate({ to: "/partner", search: { role: "captain" } })}
              >
                {t("society.becomeCaptain")}
              </Button>
            </div>
          )}
        </div>
      </section>
      {live ? (
        <>
          <section className="flex flex-col gap-2">
            <strong>{t("onboarding.unlock.invite")}</strong>
            <p className="text-sm leading-5 text-fg-secondary">{t("society.share.reward")}</p>
            <div className="flex items-center justify-between gap-2 rounded-sm border border-line-strong bg-surface py-1 pr-1 pl-3">
              <span className="min-w-0 truncate text-sm">{link.replace(/^https?:\/\//, "")}</span>
              <Button variant="quiet" onPress={copyLink}>
                <CopyIcon aria-hidden size={18} />
                {t("society.share.copyShort")}
              </Button>
            </div>
          </section>
          {impact.data?.items_donated != null && (
            <section className="flex flex-col gap-1 rounded-md bg-surface px-4 py-3">
              <strong>{t("society.impact.title", { society: s.name })}</strong>
              <p className="text-sm leading-5 text-fg-secondary">
                {t("society.impact.body", {
                  kg: impact.data.hub?.food_saved_kg ?? 0,
                  n: impact.data.items_donated,
                })}
                {impact.data.hub?.partners_earned_paise != null && (
                  <>
                    {" "}
                    <MoneyText paise={impact.data.hub.partners_earned_paise} />{" "}
                    {t("society.impact.cooks")}
                  </>
                )}
              </p>
              <Link
                to="/kindness/impact"
                className="flex min-h-12 items-center font-semibold text-link underline underline-offset-3 outline-none focus-visible:shadow-focus"
              >
                {t("impact.link")}
              </Link>
            </section>
          )}
          <Button variant="primary" size="lg" block onPress={() => setSharing(true)}>
            {t("onboarding.unlock.invite")}
          </Button>
        </>
      ) : (
        <section className="flex flex-col gap-1 rounded-md bg-surface p-4">
          <strong>{t("society.planTitle")}</strong>
          <p className="text-sm leading-[22px] text-fg-secondary">{t("society.planBody")}</p>
        </section>
      )}
      <ShareSheet open={sharing} onClose={() => setSharing(false)} society={s} unlock={unlock} />
    </div>
  );
}

/** "MT" for Meena tai, "मी" for मीना ताई. @param {string} name */
function initials(name) {
  const words = name.split(/\s+/).filter(Boolean);
  return words.length > 1 && /^[A-Za-z]/.test(name)
    ? `${words[0][0]}${words[1][0]}`.toUpperCase()
    : (words[0]?.slice(0, 2) ?? "");
}
