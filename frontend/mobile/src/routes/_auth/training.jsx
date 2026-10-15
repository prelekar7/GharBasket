import { Button, IconButton, Skeleton } from "@gharbasket/ui";
import { XIcon } from "@phosphor-icons/react";
import { createFileRoute, Navigate, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { RoleFrame } from "@/components/RoleFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useTrainings } from "@/features/partners/queries.js";
import { TrainingQuiz } from "@/features/partners/TrainingQuiz.jsx";

/** Partner training: the next required module as a quiz (design HcPartnerQuiz, M8 US-3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/training")({ component: Training });

function Training() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const trainings = useTrainings();
  const close = () => navigate({ to: "/partner" });

  const closeButton = (
    <IconButton label={t("training.close")} variant="quiet" onPress={close}>
      <XIcon size={24} />
    </IconButton>
  );
  /** @param {import("react").ReactNode} body */
  const frame = (body) => (
    <RoleFrame userRole="customer">
      <main className="flex flex-1 flex-col gap-3.5 px-5 pt-2 pb-[calc(16px+env(safe-area-inset-bottom))]">
        {body}
      </main>
    </RoleFrame>
  );
  const wait = (/** @type {import("react").ReactNode} */ body) =>
    frame(
      <>
        <div className="-ml-3 py-2">{closeButton}</div>
        {body}
      </>,
    );
  if (trainings.isPending) return wait(<Skeleton lines={8} />);
  if (trainings.isError) return wait(<ScreenError onRetry={() => trainings.refetch()} />);

  const { modules } = trainings.data;
  // No modules means no application or partner record yet: training comes after applying.
  if (modules.length === 0) return <Navigate to="/partner" />;
  const m = modules.find((x) => !x.result?.passed);
  if (!m)
    return frame(
      <>
        <header className="-ml-3 flex items-center gap-1 py-2">
          {closeButton}
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            {t("training.doneTitle")}
          </h1>
        </header>
        <p className="text-fg-secondary">{t("training.doneBody")}</p>
        <div className="mt-auto">
          <Button variant="primary" size="lg" block onPress={close}>
            {t("training.back")}
          </Button>
        </div>
      </>,
    );
  return frame(
    <TrainingQuiz
      key={m.code}
      module={m}
      position={{ n: modules.indexOf(m) + 1, total: modules.length }}
      onClose={close}
    />,
  );
}
