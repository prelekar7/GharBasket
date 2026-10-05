import { Badge, Button, ProgressBar, Skeleton } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dateLabel } from "@/features/kitchen/format.js";
import { usePartnerMe } from "@/features/kitchen/queries.js";
import { Muted, Row } from "@/features/kitchen/ui.jsx";
import { useTrainings } from "@/features/partners/queries.js";

/** Training: required modules with quiz scores, and the next refresher (design KitchenTraining; M8 US-3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/training")({ component: KitchenTraining });

function KitchenTraining() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const me = usePartnerMe();
  const trainings = useTrainings();

  if (me.isPending || trainings.isPending) return <Skeleton lines={8} />;
  if (me.isError || trainings.isError) {
    return (
      <ScreenError
        onRetry={() => {
          me.refetch();
          trainings.refetch();
        }}
      />
    );
  }
  const { modules, required_done: done, required_total: total, verified } = trainings.data;
  const next = modules.find((m) => !m.result?.passed);
  /** @type {Record<string, "primary" | "accent" | "outline">} */
  const tone = { done: "primary", next: "accent", todo: "outline" };
  const meta = (/** @type {import("@/features/partners/queries.js").TrainingModule} */ m) => {
    if (m.result?.passed) return t("kitchen.training.quiz", m.result);
    const p = { kind: m.kind, minutes: m.minutes };
    return m.due
      ? t("kitchen.training.todoDue", { ...p, date: dateLabel(m.due, lang) })
      : t("kitchen.training.todo", p);
  };

  return (
    <>
      <FieldHeader
        back={{ label: t("kitchen.training.back"), onPress: () => navigate({ to: "/kitchen" }) }}
        eyebrow={t("kitchen.whoRole", { name: me.data.display_name })}
        title={t("kitchen.training.title")}
      />
      <ProgressBar
        tone="accent"
        label={t("kitchen.training.done")}
        value={done}
        max={total}
        valueLabel={t("kitchen.training.doneValue", { done, total })}
      />
      <ul>
        {modules.map((m) => {
          const state = m.result?.passed ? "done" : m === next ? "next" : "todo";
          return (
            <Row
              key={m.code}
              tall
              title={m.title}
              sub={meta(m)}
              end={<Badge tone={tone[state]}>{t(`kitchen.training.badge.${state}`)}</Badge>}
            />
          );
        })}
      </ul>
      <Muted>{verified ? t("kitchen.training.why") : t("kitchen.training.whyPending")}</Muted>
      {next && (
        <div className="mt-auto pt-2">
          <Button
            variant="primary"
            size="lg"
            block
            onPress={() => navigate({ to: "/kitchen/training/$code", params: { code: next.code } })}
          >
            {t("kitchen.training.next", { name: next.title })}
          </Button>
        </div>
      )}
    </>
  );
}
