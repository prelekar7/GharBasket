import { Button, IconButton, SegmentedControl, Sheet, Skeleton, toast } from "@gharbasket/ui";
import { ArrowLeftIcon, CaretRightIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import {
  addWeeks,
  HOUSEHOLD_STEPS_EDIT,
  householdValue,
  useMyHousehold,
  usePause,
} from "@/features/identity/profile.js";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { shortDate, weekdayOf } from "@/features/subscriptions/format.js";

/** Household and food: the six things that shape the plan, and Pause Autopilot (design HcHousehold, M1, M3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/household")({ component: Household });

function Household() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const navigate = useNavigate();
  const { data: hh, isPending, isError, refetch } = useMyHousehold();
  const pause = usePause(hh?.subscription.id);
  const startEdit = useOnboardingDraft((d) => d.startEdit);
  const [pausing, setPausing] = useState(false);
  const [weeks, setWeeks] = useState("2");
  const paused = hh?.subscription.status === "paused";
  const backOn = addWeeks(weeks);
  const pausedUntil = hh?.subscription.paused_until
    ? shortDate(hh.subscription.paused_until, lang)
    : "";

  /** @type {{ key: string, label: string, value: string, onPress: () => void }[]} */
  const rows = hh
    ? [
        ...HOUSEHOLD_STEPS_EDIT.map(({ key, to }) => ({
          key,
          label: t(`profile.edit.${key}`),
          value: householdValue(t, key, hh),
          onPress: () => {
            if (key !== "budget") startEdit(hh);
            navigate({ to });
          },
        })),
        {
          key: "autopilot",
          label: t("profile.pause.row"),
          value: paused
            ? t("profile.pause.pausedValue", { date: pausedUntil })
            : t("profile.pause.on"),
          onPress: () => setPausing(true),
        },
      ]
    : [];

  return (
    <div className="flex flex-col gap-3">
      <header className="-ml-3 flex items-center gap-1">
        <IconButton
          label={t("onboarding.back")}
          variant="quiet"
          onPress={() => router.history.back()}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("profile.household")}
        </h1>
      </header>
      {isPending ? (
        <Skeleton lines={6} />
      ) : isError || !hh ? (
        <ScreenError onRetry={() => refetch()} />
      ) : (
        <>
          <p className="text-sm leading-5 text-fg-secondary">{t("profile.edit.lead")}</p>
          <ul className="-mt-1">
            {rows.map((r) => (
              <li key={r.key}>
                <button
                  type="button"
                  onClick={r.onPress}
                  className="flex min-h-14 w-full items-center gap-3 border-b border-line py-3 text-left outline-none focus-visible:shadow-focus [html[data-senior]_&]:min-h-16"
                >
                  <span className="flex grow flex-col">
                    <strong>{r.label}</strong>
                    <span className="text-sm leading-5 text-fg-secondary">{r.value}</span>
                  </span>
                  <CaretRightIcon aria-hidden size={20} className="shrink-0 text-fg-muted" />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <Sheet
        open={pausing}
        onOpenChange={setPausing}
        title={t("profile.pause.title")}
        description={t(paused ? "profile.pause.pausedLead" : "profile.pause.lead", {
          date: pausedUntil,
        })}
        footer={
          paused ? (
            <Button
              variant="primary"
              size="lg"
              block
              isPending={pause.isPending}
              onPress={() =>
                pause.mutate(null, {
                  onSuccess: () => {
                    setPausing(false);
                    toast(t("profile.pause.resumed"));
                  },
                  onError: () => toast(t("profile.saveFailed")),
                })
              }
            >
              {t("profile.pause.resume")}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="lg"
              block
              isPending={pause.isPending}
              onPress={() =>
                pause.mutate(backOn, {
                  onSuccess: (sub) => {
                    setPausing(false);
                    toast(
                      t("profile.pause.paused", { date: shortDate(sub.paused_until ?? "", lang) }),
                    );
                  },
                  onError: () => toast(t("profile.saveFailed")),
                })
              }
            >
              {t("profile.pause.cta", { n: Number(weeks) })}
            </Button>
          )
        }
      >
        {!paused && (
          <div className="flex flex-col gap-3">
            <SegmentedControl
              label={t("profile.pause.title")}
              value={weeks}
              onChange={setWeeks}
              options={["1", "2", "4"].map((w) => ({
                value: w,
                label: t("profile.pause.weeks", { n: Number(w) }),
              }))}
            />
            <p className="text-sm leading-5 text-fg-secondary">
              {t("profile.pause.until", {
                date: `${t(`days.short.d${weekdayOf(backOn)}`)} ${shortDate(backOn, lang)}`,
              })}
            </p>
          </div>
        )}
      </Sheet>
    </div>
  );
}
