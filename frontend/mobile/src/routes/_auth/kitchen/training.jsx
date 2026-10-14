import { Badge, Button, ProgressBar, Sheet, Skeleton, toast } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { dateLabel } from "@/features/kitchen/format.js";
import { useKitchenWrite, usePartnerMe, useTrainings } from "@/features/kitchen/queries.js";
import { Muted, Row } from "@/features/kitchen/ui.jsx";

/** Training: required modules with quiz scores, and the next refresher (design KitchenTraining; M8 US-3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen/training")({ component: KitchenTraining });

function KitchenTraining() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const me = usePartnerMe();
  const trainings = useTrainings();
  const write = useKitchenWrite();
  const [open, setOpen] = useState(false);

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
  const { items, required_done: done, required_total: total, verified } = trainings.data;
  const next = items.find((m) => !m.done);
  const title = (/** @type {string} */ code) =>
    t(`kitchen.training.modules.${code}`, { defaultValue: code });
  const meta = (/** @type {import("@/features/kitchen/queries.js").TrainingModule} */ m) => {
    if (m.done) {
      return m.score === null
        ? t("kitchen.training.finished")
        : t("kitchen.training.quiz", { score: m.score, total: m.out_of });
    }
    const p = { kind: m.kind, minutes: m.minutes };
    return m.due
      ? t("kitchen.training.todoDue", { ...p, date: dateLabel(m.due, lang) })
      : t("kitchen.training.todo", p);
  };
  const complete = () => {
    if (!next) return;
    setOpen(false);
    write({
      name: "trainings",
      path: `/v1/trainings/${next.code}/complete`,
      body: {},
      update: (/** @type {import("@/features/kitchen/queries.js").Trainings} */ d) => ({
        ...d,
        required_done: d.required_done + (next.kind === "required" ? 1 : 0),
        items: d.items.map((m) => (m.code === next.code ? { ...m, done: true, score: null } : m)),
      }),
    });
    toast(t("kitchen.training.doneToast"));
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
        {items.map((m) => (
          <Row
            key={m.code}
            tall
            title={title(m.code)}
            sub={meta(m)}
            end={
              <Badge tone={m.done ? "primary" : m === next ? "accent" : "outline"}>
                {t(`kitchen.training.badge.${m.done ? "done" : m === next ? "next" : "todo"}`)}
              </Badge>
            }
          />
        ))}
      </ul>
      <Muted>{verified ? t("kitchen.training.why") : t("kitchen.training.whyPending")}</Muted>
      {next && (
        <div className="mt-auto pt-2">
          <Button variant="primary" size="lg" block onPress={() => setOpen(true)}>
            {t("kitchen.training.next", { name: title(next.code) })}
          </Button>
        </div>
      )}
      <Sheet
        open={open}
        onOpenChange={setOpen}
        title={next ? title(next.code) : ""}
        description={next ? t("kitchen.training.about", { minutes: next.minutes }) : undefined}
        footer={
          <Button variant="primary" size="lg" block onPress={complete}>
            {t("kitchen.training.markDone")}
          </Button>
        }
      >
        <Muted>{t("kitchen.training.soon")}</Muted>
      </Sheet>
    </>
  );
}
