import { Badge, Button, Dialog, formatPaise, IconButton, Skeleton, toast } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useCancelMandate, useCreateMandate, useMandate } from "@/features/billing/queries.js";
import { useBudgetLock } from "@/features/subscriptions/queries.js";
import { AUTOPAY } from "@/lib/features.js";

/** Without a Budget Lock, the mandate's monthly ceiling (our proposal; M5 mandates.max_paise). */
const DEFAULT_MAX_PAISE = 500000;

/** UPI Autopay: what it means, set it up or keep the wallet, cancel any time (design HcAutopay, M5 US-3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/autopay")({
  // Pilot only (lib/features.js): without the flag the screen does not exist.
  beforeLoad: () => {
    if (!AUTOPAY) throw redirect({ to: "/profile" });
  },
  component: Autopay,
});

function Autopay() {
  const { t } = useTranslation();
  const router = useRouter();
  const navigate = useNavigate();
  const mandate = useMandate();
  const lock = useBudgetLock();
  const create = useCreateMandate();
  const cancel = useCancelMandate();
  const [confirm, setConfirm] = useState(/** @type {"create" | "cancel" | null} */ (null));

  const header = (
    <header className="-ml-3 flex items-center justify-between gap-2">
      <div className="flex items-center gap-1">
        <IconButton
          label={t("onboarding.back")}
          variant="quiet"
          onPress={() => router.history.back()}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("autopay.title")}
        </h1>
      </div>
      <Badge tone="accent">{t("autopay.pilot")}</Badge>
    </header>
  );
  if (mandate.isPending || lock.isPending)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  if (mandate.isError)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError onRetry={() => mandate.refetch()} />
      </div>
    );

  const active = mandate.data;
  // The mandate's ceiling follows the Budget Lock when there is one (design: "Up to your ₹3,000").
  const budget = lock.data?.budget_paise ?? null;
  const max = active?.max_paise ?? budget ?? DEFAULT_MAX_PAISE;
  const facts = [
    ["charged", t("autopay.facts.charged.v")],
    [
      "limit",
      budget !== null && max === budget
        ? t("autopay.facts.limit.v", { amount: formatPaise(max) })
        : t("autopay.facts.limit.vNoLock", { amount: formatPaise(max) }),
    ],
    ["notice", t("autopay.facts.notice.v")],
    ["cancel", t("autopay.facts.cancel.v")],
  ];

  return (
    <div className="flex min-h-[calc(100dvh-160px)] flex-col gap-3.5">
      {header}
      <p className="text-sm leading-5 text-fg-secondary">
        {t(active ? "autopay.leadOn" : "autopay.lead")}
      </p>
      <dl className="flex flex-col rounded-md bg-surface px-4 py-1">
        {facts.map(([key, value], i) => (
          <div
            key={key}
            className={`flex items-baseline justify-between gap-3 py-2.5 text-sm leading-5 ${i < facts.length - 1 ? "border-b border-line" : ""}`}
          >
            <dt className="whitespace-nowrap text-fg-secondary">{t(`autopay.facts.${key}.k`)}</dt>
            <dd className="text-right font-semibold">{value}</dd>
          </div>
        ))}
      </dl>
      {!active && <p className="text-sm leading-5 text-fg-secondary">{t("autopay.upi")}</p>}
      <div className="mt-auto flex flex-col gap-1">
        {active ? (
          <Button
            variant="secondary"
            size="lg"
            block
            isPending={cancel.isPending}
            onPress={() => setConfirm("cancel")}
          >
            {t("autopay.cancelCta")}
          </Button>
        ) : (
          <>
            <Button
              variant="primary"
              size="lg"
              block
              isPending={create.isPending}
              onPress={() => setConfirm("create")}
            >
              {t("autopay.cta")}
            </Button>
            <Button variant="quiet" block onPress={() => navigate({ to: "/wallet" })}>
              {t("autopay.wallet")}
            </Button>
          </>
        )}
      </div>

      {/* A mandate moves money, so both directions confirm (handbook 09: money actions use a Dialog). */}
      <Dialog
        isOpen={confirm === "create"}
        onOpenChange={(o) => setConfirm(o ? "create" : null)}
        title={t("autopay.confirmTitle", { amount: formatPaise(max) })}
        confirmLabel={t("autopay.confirmCta", { amount: formatPaise(max) })}
        cancelLabel={t("autopay.confirmCancel")}
        onConfirm={() =>
          create.mutate(max, {
            onSuccess: () => toast(t("autopay.done")),
            onError: () => toast(t("autopay.failed")),
          })
        }
      >
        {t("autopay.confirmBody")}
      </Dialog>
      <Dialog
        isOpen={confirm === "cancel"}
        onOpenChange={(o) => setConfirm(o ? "cancel" : null)}
        tone="danger"
        title={t("autopay.cancelTitle")}
        confirmLabel={t("autopay.cancelConfirm")}
        cancelLabel={t("autopay.cancelKeep")}
        onConfirm={() =>
          active &&
          cancel.mutate(active.id, {
            onSuccess: () => toast(t("autopay.cancelled")),
            onError: () => toast(t("autopay.cancelFailed")),
          })
        }
      >
        {t("autopay.cancelBody")}
      </Dialog>
    </div>
  );
}
