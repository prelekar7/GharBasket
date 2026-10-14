import {
  Button,
  formatPaise,
  IconButton,
  ProgressBar,
  RadioCards,
  Skeleton,
  TextField,
  toast,
} from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useBudgetLock, useSaveBudgetLock } from "@/features/subscriptions/queries.js";

/** @typedef {import("@/features/subscriptions/queries.js").WhenOver} WhenOver */
const WHEN_OVER = /** @type {WhenOver[]} */ (["swap", "trim", "ask"]);
const MIN_PAISE = 50000;
const DEFAULT_PAISE = 300000;
const rupees = new Intl.NumberFormat("en-IN");

/** Budget Lock: this month so far, the monthly limit, and what Autopilot does when a week would go over (design HcBudget, M3 US-6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/budget")({ component: BudgetLock });

function BudgetLock() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const lock = useBudgetLock();
  const save = useSaveBudgetLock();
  // Untouched fields show what is saved; null means "not changed yet".
  const [limit, setLimit] = useState(/** @type {number | null} */ (null));
  const [whenOver, setWhenOver] = useState(/** @type {WhenOver | null} */ (null));

  const header = (
    <header className="-ml-3 flex items-center gap-1">
      <IconButton
        label={t("onboarding.back")}
        variant="quiet"
        onPress={() => router.history.back()}
      >
        <ArrowLeftIcon size={24} />
      </IconButton>
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("budget.title")}</h1>
    </header>
  );
  if (lock.isPending)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  if (lock.isError || !lock.data)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError onRetry={() => lock.refetch()} />
      </div>
    );

  const b = lock.data;
  const paise = limit ?? b.budget_paise ?? DEFAULT_PAISE;
  const mode = whenOver ?? b.when_over;
  const month = new Intl.DateTimeFormat(lang, { month: "long", timeZone: "UTC" }).format(
    new Date(`${b.month}-15T00:00:00Z`),
  );
  const left = (b.budget_paise ?? 0) - b.spent_paise;
  const store = (/** @type {number | null} */ budget) =>
    save.mutate(
      { budget_paise: budget, when_over: mode },
      {
        onSuccess: () => {
          setLimit(null);
          setWhenOver(null);
          toast(t(budget === null ? "budget.turnedOff" : "budget.saved"));
        },
        onError: () => toast(t("profile.saveFailed")),
      },
    );

  return (
    <div className="flex min-h-[calc(100dvh-160px)] flex-col gap-3.5">
      {header}
      {b.budget_paise === null ? (
        <p className="rounded-md bg-surface px-4 py-3.5 text-sm leading-5 text-fg-secondary">
          {t("budget.off")}
        </p>
      ) : (
        <section className="flex flex-col gap-2 rounded-md bg-surface px-4 py-3.5">
          <span className="text-sm leading-5 text-fg-secondary">{month}</span>
          <ProgressBar
            label={t("budget.spent")}
            value={Math.min(b.spent_paise, b.budget_paise)}
            max={b.budget_paise}
            tone={left < 0 ? "danger" : "primary"}
            valueLabel={t("budget.spentValue", {
              spent: formatPaise(b.spent_paise),
              limit: formatPaise(b.budget_paise),
            })}
          />
          <span className="text-sm leading-5 text-fg-secondary">
            {left >= 0
              ? t("budget.left", { amount: formatPaise(left), n: b.delivery_days_left })
              : t("budget.over", { amount: formatPaise(-left) })}
          </span>
        </section>
      )}
      <TextField
        label={t("budget.limitLabel")}
        description={t("budget.limitHelp")}
        prefix="₹"
        inputMode="numeric"
        value={rupees.format(paise / 100)}
        onChange={(v) => setLimit(Number(v.replace(/\D/g, "").slice(0, 6) || 0) * 100)}
        isInvalid={paise < MIN_PAISE}
        errorMessage={t("onboarding.budget.min", { amount: formatPaise(MIN_PAISE) })}
      />
      <h2 className="text-lg leading-[26px] font-bold">{t("budget.tightTitle")}</h2>
      <RadioCards
        label={t("budget.tightTitle")}
        value={mode}
        onChange={(v) => setWhenOver(/** @type {WhenOver} */ (v))}
        options={WHEN_OVER.map((value) => ({
          value,
          title: t(`budget.tight.${value}.title`),
          description: t(`budget.tight.${value}.body`),
        }))}
      />
      <div className="mt-auto flex flex-col gap-1">
        <Button
          variant="primary"
          size="lg"
          block
          isPending={save.isPending && save.variables?.budget_paise !== null}
          isDisabled={paise < MIN_PAISE}
          onPress={() => store(paise)}
        >
          {t("budget.save")}
        </Button>
        {b.budget_paise !== null && (
          <Button
            variant="quiet"
            block
            isPending={save.isPending && save.variables?.budget_paise === null}
            onPress={() => store(null)}
          >
            {t("budget.turnOff")}
          </Button>
        )}
      </div>
    </div>
  );
}
