import { Button, IconButton, OrderTimeline, RadioCards, Skeleton, toast } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useMyHousehold } from "@/features/identity/profile.js";
import { useApply, useMyApplication } from "@/features/partners/queries.js";
import { shortDate } from "@/features/subscriptions/format.js";

/** @typedef {import("@/features/partners/queries.js").PartnerKind} PartnerKind */
const KINDS = /** @type {PartnerKind[]} */ (["captain", "kitchen", "rider", "packer"]);

/** Become a partner: pick a role, see what happens next, apply (design HcPartner, M8, W7). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/partner")({
  validateSearch: (/** @type {Record<string, unknown>} */ s) => ({
    role: KINDS.includes(/** @type {PartnerKind} */ (s.role))
      ? /** @type {PartnerKind} */ (s.role)
      : undefined,
  }),
  component: Partner,
});

const STEPS = ["apply", "kyc", "training", "verified"];
/** Which step an application is on (M8 §6.6); REJECTED shows as a problem at KYC. */
const STEP_OF = /** @type {Record<string, number>} */ ({
  APPLIED: 1,
  KYC_PENDING: 1,
  REJECTED: 1,
  TRAINING: 2,
  VERIFIED: 4,
  ACTIVE: 4,
  PAUSED: 4,
});

function Partner() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { role } = Route.useSearch();
  const [kind, setKind] = useState(/** @type {PartnerKind} */ (role ?? "captain"));
  const application = useMyApplication();
  const household = useMyHousehold();
  const apply = useApply();

  const header = (
    <header className="-ml-3 flex items-center gap-1">
      <IconButton
        label={t("onboarding.back")}
        variant="quiet"
        onPress={() => router.history.back()}
      >
        <ArrowLeftIcon size={24} />
      </IconButton>
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">
        {t("partner.title")}
      </h1>
    </header>
  );
  if (application.isPending)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  if (application.isError)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError onRetry={() => application.refetch()} />
      </div>
    );

  const app = application.data;
  const at = app ? (STEP_OF[app.status] ?? 1) : 0;
  const steps = STEPS.map((s, i) => ({
    label: t(`partner.steps.${s}`),
    state: /** @type {"done" | "current" | "issue" | "todo"} */ (
      i < at ? "done" : i === at ? (app?.status === "REJECTED" ? "issue" : "current") : "todo"
    ),
  }));

  return (
    <div className="flex min-h-[calc(100dvh-160px)] flex-col gap-3">
      {header}
      {app ? (
        <p className="text-sm leading-[22px] text-fg-secondary">
          {t(app.status === "REJECTED" ? "partner.rejected" : "partner.applied", {
            role: t(`partner.roles.${app.kind}.title`),
            date: shortDate(app.submitted_at.slice(0, 10), lang),
          })}
        </p>
      ) : (
        <>
          <p className="text-sm leading-[22px] text-fg-secondary">{t("partner.intro")}</p>
          {/* Compact cards, as in the design, so the steps and the button fit on one screen. */}
          <div className="[&_label]:min-h-0 [&_label]:py-2">
            <RadioCards
              label={t("partner.title")}
              value={kind}
              onChange={(v) => setKind(/** @type {PartnerKind} */ (v))}
              options={KINDS.map((value) => ({
                value,
                title: t(`partner.roles.${value}.title`),
                description: t(`partner.roles.${value}.body`),
              }))}
            />
          </div>
        </>
      )}
      <section className="flex flex-col gap-2 rounded-md bg-surface px-4 py-3">
        <strong>{t("partner.nextTitle")}</strong>
        <OrderTimeline
          steps={steps}
          label={t("partner.nextTitle")}
          labels={{
            done: t("orders.tl.done"),
            current: t("orders.tl.current"),
            issue: t("orders.tl.issue"),
            todo: t("orders.tl.todo"),
          }}
        />
      </section>
      {!app && (
        <div className="mt-auto">
          <Button
            variant="primary"
            size="lg"
            block
            isPending={apply.isPending}
            onPress={() =>
              apply.mutate(
                {
                  kind,
                  // A captain serves their own society; the other roles work from the hub.
                  scopeId: kind === "captain" ? household.data?.society_id : undefined,
                },
                {
                  onSuccess: () => toast(t("partner.sent")),
                  onError: () => toast(t("partner.failed")),
                },
              )
            }
          >
            {t("partner.cta", { role: t(`partner.roles.${kind}.title`) })}
          </Button>
        </div>
      )}
    </div>
  );
}
