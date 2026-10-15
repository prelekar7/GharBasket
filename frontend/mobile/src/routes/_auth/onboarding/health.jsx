import { Button, Chip, ConsentCard } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { MEMBER_GROUPS, useOnboardingDraft } from "@/features/onboarding/draft.js";
import { useStepGo } from "@/features/onboarding/useStepGo.js";

const TAGS = ["diabetes", "high_bp", "high_protein", "weight_loss", "low_oil"];
/** Tags shown before "More" (Hf09); a chosen tag always stays visible. */
const FIRST = 3;
/** People in the household, keyed as in memberKeys (draft.js), with their group for the label. */
function people(/** @type {import("@/features/onboarding/draft.js").Members} */ members) {
  return MEMBER_GROUPS.flatMap((g) =>
    Array.from({ length: members[g] }, (_, i) => ({ key: `${g}-${i + 1}`, group: g, n: i + 1 })),
  );
}

/**
 * Any health needs? Optional, and only with consent (DPDP): the switch starts off, and the tags
 * appear only once it is on. Turning it off or skipping clears every tag (design Hf09).
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/health")({ component: Health });

function Health() {
  const { t } = useTranslation();
  const _navigate = useNavigate();
  const { household: h, updateHousehold } = useOnboardingDraft();
  const step = useStepGo("/onboarding/health");
  const go = step.go;
  const [open, setOpen] = useState(/** @type {string[]} */ ([]));

  /** @param {string} person @param {string} tag @param {boolean} on */
  const setTag = (person, tag, on) => {
    const tags = h.health[person] ?? [];
    updateHousehold({
      health: { ...h.health, [person]: on ? [...tags, tag] : tags.filter((x) => x !== tag) },
    });
  };

  return (
    <OnboardingFrame
      step={3}
      footer={
        <>
          <Button variant="primary" size="lg" block isPending={step.busy} onPress={go}>
            {step.label}
          </Button>
          {!step.editing && (
            <Button
              variant="quiet"
              block
              onPress={() => {
                updateHousehold({ healthConsent: false, health: {} });
                go();
              }}
            >
              {t("onboarding.health.skip")}
            </Button>
          )}
        </>
      }
    >
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">
        {t("onboarding.health.title")}
      </h1>
      <ConsentCard
        title={t("onboarding.health.cTitle")}
        why={t("onboarding.health.why")}
        uses={[
          t("onboarding.health.use1"),
          t("onboarding.health.use2"),
          t("onboarding.health.use3"),
        ]}
        version={t("onboarding.health.version")}
        switchLabel={t("onboarding.health.switchLabel")}
        granted={h.healthConsent}
        onChange={(on) => updateHousehold({ healthConsent: on, health: on ? h.health : {} })}
        labels={{ optional: t("onboarding.health.optional"), why: t("onboarding.health.whyLead") }}
      />
      {h.healthConsent &&
        people(h.members).map((p) => (
          <div key={p.key} className="flex flex-col gap-2">
            <span className="text-sm leading-5 font-semibold">
              {t(`onboarding.health.person.${p.group}`, { n: p.n })}
            </span>
            <div className="flex flex-wrap gap-2">
              {TAGS.filter(
                (tag, i) =>
                  i < FIRST || open.includes(p.key) || (h.health[p.key] ?? []).includes(tag),
              ).map((tag) => (
                <Chip
                  key={tag}
                  isSelected={(h.health[p.key] ?? []).includes(tag)}
                  onChange={(on) => setTag(p.key, tag, on)}
                >
                  {t(`onboarding.health.tags.${tag}`)}
                </Chip>
              ))}
              {!open.includes(p.key) &&
                TAGS.some((tag, i) => i >= FIRST && !(h.health[p.key] ?? []).includes(tag)) && (
                  <Chip isSelected={false} onChange={() => setOpen((o) => [...o, p.key])}>
                    {t("onboarding.health.tags.more")}
                  </Chip>
                )}
            </div>
          </div>
        ))}
    </OnboardingFrame>
  );
}
