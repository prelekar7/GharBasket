import { Button, Switch } from "@gharbasket/ui";
import { LockSimpleIcon } from "@phosphor-icons/react";
import { useMutation } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { hasConsent, nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";
import { submitOnboarding } from "@/features/onboarding/submit.js";
import { ApiError } from "@/lib/api.js";

/**
 * Your data, your choice (DPDP, design Hf12). Delivery and personalised plans are needed
 * (service_delivery, personalisation); every optional use is a switch. "Agree and continue"
 * creates the household with the chosen consents in one call, once.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/consent")({ component: Consent });

function Consent() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const draft = useOnboardingDraft();
  const { household: h, setConsent, update } = draft;
  const save = useMutation({
    mutationFn: async () => {
      const state = useOnboardingDraft.getState();
      // Already created (the person came back to this screen): don't create it again.
      if (state.householdId) return { id: state.householdId };
      return submitOnboarding(state);
    },
    onSuccess: (household) => {
      update({ householdId: household.id });
      navigate({ to: nextStep("/onboarding/consent") });
    },
  });
  // The server's privacy notice moved on: the app shows an old one, so say so instead of retrying.
  const stale =
    save.error instanceof ApiError && save.error.code === "identity.consent_version_stale";

  /** @type {{ purpose: "health_personalisation" | "marketing" | "research_anonymised", label: string, desc: string }[]} */
  const optional = [
    {
      purpose: "health_personalisation",
      label: t("onboarding.consent.health"),
      desc: t("onboarding.consent.healthDesc"),
    },
    {
      purpose: "marketing",
      label: t("onboarding.consent.whatsapp"),
      desc: t("onboarding.consent.whatsappDesc"),
    },
    {
      purpose: "research_anonymised",
      label: t("onboarding.consent.insights"),
      desc: t("onboarding.consent.insightsDesc"),
    },
  ];

  return (
    <OnboardingFrame
      footer={
        <>
          {save.isError && (
            <p role="alert" className="text-sm font-semibold text-danger">
              {t(stale ? "onboarding.consent.stale" : "onboarding.consent.failed")}
            </p>
          )}
          <Button
            variant="primary"
            size="lg"
            block
            isPending={save.isPending}
            onPress={() => save.mutate()}
          >
            {t("onboarding.consent.cta")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("onboarding.consent.title")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.consent.sub")}</p>
      </div>
      <section className="flex flex-col gap-2">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("onboarding.consent.needed")}
        </span>
        <div className="flex items-start gap-3 rounded-md bg-surface p-4">
          <div className="flex flex-1 flex-col">
            <strong>{t("onboarding.consent.accTitle")}</strong>
            <span className="text-sm leading-[22px] text-fg-secondary">
              {t("onboarding.consent.accBody")}
            </span>
          </div>
          <span className="inline-flex shrink-0 items-center gap-1 text-xs leading-[18px] font-semibold text-fg-secondary">
            <LockSimpleIcon aria-hidden size={14} />
            {t("onboarding.consent.always")}
          </span>
        </div>
      </section>
      <section className="flex flex-col">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("onboarding.consent.optional")}
        </span>
        {optional.map((o) => (
          <div key={o.purpose} className="border-b border-line py-1 last:border-b-0">
            <Switch
              label={o.label}
              description={o.desc}
              isSelected={hasConsent(h, o.purpose)}
              onChange={(on) => setConsent(o.purpose, on)}
            />
          </div>
        ))}
      </section>
      <p className="text-sm leading-[22px] text-fg-secondary">
        {t("onboarding.consent.pre")}
        <a href="/privacy" className="font-semibold text-link underline">
          {t("onboarding.consent.notice")}
        </a>
        {t("onboarding.consent.post")}
      </p>
    </OnboardingFrame>
  );
}
