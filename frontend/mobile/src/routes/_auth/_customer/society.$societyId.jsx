import {
  Button,
  IconButton,
  MoneyText,
  Skeleton,
  SocietyUnlockBar,
  StatusPill,
  toast,
} from "@gharbasket/ui";
import { ArrowLeftIcon, CopyIcon, MedalIcon, WhatsappLogoIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { inviteLink, whatsappChat } from "@/features/community/invite.js";
import { useImpact, useRecordInvite, useSociety } from "@/features/community/queries.js";
import { ShareSheet } from "@/features/community/ShareSheet.jsx";
import { UnlockCelebration } from "@/features/community/UnlockCelebration.jsx";
import { shortDate, timeRange, weekdayName } from "@/features/subscriptions/format.js";

/**
 * Your society: unlock progress or, once live, delivery days, the captain, the invite link and
 * this month's impact (designs HcSociety, Core-S1); the invite sheet (S3) and the one-time unlock
 * celebration (S4). M9.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/society/$societyId")({ component: Society });

/** A society counts as "just unlocked" for a week after it goes live. */
const CELEBRATE_FOR_MS = 7 * 86_400_000;
const seenKey = (/** @type {string} */ id) => `gb-unlock-seen-${id}`;

function Society() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const navigate = useNavigate();
  const { societyId } = Route.useParams();
  const { data: s, isPending, isError, refetch } = useSociety(societyId);
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

  if (isPending) return <Skeleton lines={6} />;
  if (isError || !s) return <ScreenError onRetry={() => refetch()} />;
  const live = s.status === "live";
  const list = new Intl.ListFormat(lang, { type: "conjunction" });
  // Weekday names from a known Monday (5 Oct 2026), so they come out in the person's language.
  const days = s.delivery
    ? `${list.format(s.delivery.days.map((d) => weekdayName(`2026-10-${String(5 + d).padStart(2, "0")}`, lang)))}, ${timeRange(s.delivery, lang)}`
    : null;
  const link = inviteLink(s.id);
  const justUnlocked =
    live && !seen && !!s.unlocked_at && Date.now() - Date.parse(s.unlocked_at) < CELEBRATE_FOR_MS;

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
        homes={s.unlock.joined}
        start={
          s.delivery?.first_date
            ? `${weekdayName(s.delivery.first_date, lang)} ${shortDate(s.delivery.first_date, lang)}`
            : ""
        }
        days={days ?? ""}
        captain={s.captain ? `${s.captain.name}, ${s.captain.flat}` : null}
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
          {live ? s.name : t("society.title")}
        </h1>
      </header>
      <div className="-mt-2 flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm text-fg-secondary">{live ? t("society.free") : s.area}</p>
          <StatusPill status={live ? "delivered" : "preparing"}>
            {t(live ? "society.live" : "society.unlocking")}
          </StatusPill>
        </div>
        {live && days && (
          <p className="text-sm leading-5 text-fg-secondary">
            {t("society.delivery", { area: s.area, days })}
          </p>
        )}
      </div>
      {!live && (
        <SocietyUnlockBar
          society={s.name}
          joined={s.unlock.joined}
          target={s.unlock.threshold}
          captain={s.captain?.name}
          onInvite={() => setSharing(true)}
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
      )}
      <section className="flex gap-3.5 rounded-md bg-surface p-4">
        <div className="grid size-11 shrink-0 place-items-center rounded-full bg-accent-soft font-semibold text-accent-fg">
          {s.captain ? initials(s.captain.name) : <MedalIcon aria-hidden size={24} />}
        </div>
        <div className="flex flex-col gap-0.5">
          <strong>
            {s.captain ? t("society.captainIs", { name: s.captain.name }) : t("society.noCaptain")}
          </strong>
          <p className="text-sm leading-5 text-fg-secondary">
            {s.captain
              ? t("society.captainDoes", { flat: s.captain.flat })
              : t("society.captainBody", { society: s.name })}
          </p>
          <div>
            {s.captain?.phone_e164 ? (
              <Button
                variant="quiet"
                className="-ml-4"
                onPress={() => s.captain?.phone_e164 && whatsappChat(s.captain.phone_e164)}
              >
                <WhatsappLogoIcon aria-hidden size={20} weight="fill" />
                {t("society.whatsapp")}
              </Button>
            ) : (
              !s.captain && (
                <Button
                  variant="quiet"
                  className="-ml-4"
                  onPress={() => navigate({ to: "/partner", search: { role: "captain" } })}
                >
                  {t("society.becomeCaptain")}
                </Button>
              )
            )}
          </div>
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
          {impact.data && (
            <section className="flex flex-col gap-1 rounded-md bg-surface px-4 py-3">
              <strong>{t("society.impact.title", { society: s.name })}</strong>
              <p className="text-sm leading-5 text-fg-secondary">
                {t("society.impact.body", {
                  kg: impact.data.food_saved_kg,
                  n: impact.data.items_donated,
                })}{" "}
                <MoneyText paise={impact.data.cooks_earned_paise} /> {t("society.impact.cooks")}
              </p>
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
      <ShareSheet open={sharing} onClose={() => setSharing(false)} society={s} />
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
