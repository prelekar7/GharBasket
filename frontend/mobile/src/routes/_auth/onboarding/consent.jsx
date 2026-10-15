import { Button, Switch } from "@gharbasket/ui";
import { LockSimpleIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";
import { submitOnboarding } from "@/features/onboarding/submit.js";
import { ApiError } from "@/lib/api.js";

/** Errors with their own words; any other failure is the "check your internet" line. */
const KNOWN = [
  "identity_tower_unknown",
  "identity_address_outside_society",
  "identity_society_unknown",
  "identity_diet_conflict",
  "identity_consent_version_stale",
];

/**
 * Your data, your choice (DPDP, design Hf12). Account and delivery is needed; every optional use
 * is a switch. "Agree and continue" creates the household with the chosen consents.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/consent")({ component: Consent });

function Consent() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const draft = useOnboardingDraft();
  const { household: h, updateHousehold } = draft;
  const save = useMutation({
    mutationFn: async () => {
      try {
        await submitOnboarding(useOnboardingDraft.getState());
      } catch (e) {
        // This phone already has a household (a first try that went through): carry on with it.
        if (!(e instanceof ApiError && e.code === "identity.household_exists")) throw e;
      }
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["household", "me"] });
      navigate({ to: nextStep("/onboarding/consent") });
    },
  });
  const code = save.error instanceof ApiError ? save.error.code.replaceAll(".", "_") : "";

  /** @type {{ key: "healthConsent" | "whatsappConsent" | "insightsConsent", label: string, desc: string }[]} */
  const optional = [
    {
      key: "healthConsent",
      label: t("onboarding.consent.health"),
      desc: t("onboarding.consent.healthDesc"),
    },
    {
      key: "whatsappConsent",
      label: t("onboarding.consent.whatsapp"),
      desc: t("onboarding.consent.whatsappDesc"),
    },
    {
      key: "insightsConsent",
      label: t("onboarding.consent.insights"),
      desc: t("onboarding.consent.insightsDesc"),
    },
  ];

  return (
    <OnboardingFrame
      footer={
        <>
          <p className="mb-1 text-sm leading-5 text-fg-secondary">
            {t("onboarding.consent.pre")}
            <Link to="/legal/privacy" className="font-semibold text-link underline">
              {t("onboarding.consent.notice")}
            </Link>
            {t("onboarding.consent.post")}
          </p>
          {save.isError && (
            <p role="alert" className="text-sm font-semibold text-danger">
              {t(
                KNOWN.includes(code)
                  ? `onboarding.consent.errors.${code}`
                  : "onboarding.consent.failed",
              )}
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
          <div key={o.key} className="border-b border-line py-1 [&>label]:max-w-none">
            <Switch
              label={o.label}
              description={o.desc}
              isSelected={h[o.key]}
              onChange={(on) =>
                updateHousehold(
                  o.key === "healthConsent" && !on
                    ? { healthConsent: false, health: {} }
                    : { [o.key]: on },
                )
              }
            />
          </div>
        ))}
      </section>
    </OnboardingFrame>
  );
}
