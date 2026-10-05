import { Button, IconButton, ProgressBar, RadioCards, Skeleton, toast } from "@gharbasket/ui";
import { CheckCircleIcon, WarningCircleIcon, XIcon } from "@phosphor-icons/react";
import { createFileRoute, Navigate, useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { RoleFrame } from "@/components/RoleFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useCompleteTraining, useTrainings } from "@/features/partners/queries.js";
import { ApiError } from "@/lib/api.js";

/** Partner training: one question at a time, with the reason after each answer (design HcPartnerQuiz, M8 US-3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/training")({ component: Training });

function Training() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const trainings = useTrainings();
  const complete = useCompleteTraining();
  const [q, setQ] = useState(0);
  const [answers, setAnswers] = useState(/** @type {Record<string, string>} */ ({}));
  const [failed, setFailed] = useState(
    /** @type {{ score: number, total: number } | null} */ (null),
  );
  // One key per try at a module, so a retry after a dropped connection is not scored twice.
  const attempt = useRef(crypto.randomUUID());
  const close = () => navigate({ to: "/partner" });
  const restart = () => {
    setQ(0);
    setAnswers({});
    setFailed(null);
    attempt.current = crypto.randomUUID();
  };

  const modules = trainings.data ?? [];
  const index = modules.findIndex((m) => !m.result?.passed);
  const m = index >= 0 ? modules[index] : null;
  const header = (
    <header className="flex items-center gap-1 py-2 pr-5 pl-2">
      <IconButton label={t("training.close")} variant="quiet" onPress={close}>
        <XIcon size={24} />
      </IconButton>
      <div className="flex flex-col">
        {m && (
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("training.eyebrow", { n: index + 1, total: modules.length })}
          </span>
        )}
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {m ? m.title : t("training.doneTitle")}
        </h1>
      </div>
    </header>
  );

  /** @param {import("react").ReactNode} body */
  const frame = (body) => (
    <RoleFrame userRole="customer">
      {header}
      <main className="flex flex-1 flex-col gap-3.5 px-5 pt-2 pb-[calc(16px+env(safe-area-inset-bottom))]">
        {body}
      </main>
    </RoleFrame>
  );
  if (trainings.isPending) return frame(<Skeleton lines={8} />);
  // Training comes after applying.
  if (trainings.error instanceof ApiError && trainings.error.status === 404)
    return <Navigate to="/partner" />;
  if (trainings.isError) return frame(<ScreenError onRetry={() => trainings.refetch()} />);
  if (!m)
    return frame(
      <>
        <p className="text-fg-secondary">{t("training.doneBody")}</p>
        <div className="mt-auto">
          <Button variant="primary" size="lg" block onPress={close}>
            {t("training.back")}
          </Button>
        </div>
      </>,
    );
  if (failed)
    return frame(
      <>
        <p role="status" className="rounded-md bg-danger-soft px-3.5 py-3 font-semibold">
          {t("training.failed", failed)}
        </p>
        <div className="mt-auto">
          <Button variant="primary" size="lg" block onPress={restart}>
            {t("training.retry")}
          </Button>
        </div>
      </>,
    );

  const question = m.questions[q];
  const picked = answers[question.id];
  const right = picked === question.answer;
  const answer = question.options.find((o) => o.value === question.answer)?.title ?? "";
  const last = q === m.questions.length - 1;
  const finish = () =>
    complete.mutate(
      { code: m.code, answers, key: attempt.current },
      {
        onSuccess: (r) => {
          if (r.passed) {
            restart();
            toast(t("training.passed", r));
          } else setFailed(r);
        },
        onError: () => toast(t("training.saveFailed")),
      },
    );

  return frame(
    <>
      <ProgressBar
        label={t("training.q")}
        value={q + 1}
        max={m.questions.length}
        valueLabel={t("training.qValue", { n: q + 1, total: m.questions.length })}
      />
      <h2 className="text-lg leading-[26px] font-bold">{question.text}</h2>
      <RadioCards
        key={question.id}
        label={question.text}
        value={picked ?? ""}
        // The first answer counts; the feedback below explains it.
        onChange={(v) => !picked && setAnswers((a) => ({ ...a, [question.id]: v }))}
        options={question.options}
      />
      {picked && (
        <div role="status" className="flex flex-col gap-2">
          <p
            className={`flex gap-2.5 rounded-md px-3.5 py-3 ${right ? "bg-primary-soft" : "bg-danger-soft"}`}
          >
            {right ? (
              <CheckCircleIcon
                aria-hidden
                size={24}
                weight="fill"
                className="shrink-0 text-primary"
              />
            ) : (
              <WarningCircleIcon
                aria-hidden
                size={24}
                weight="fill"
                className="shrink-0 text-danger"
              />
            )}
            <strong>
              {t(right ? "training.right" : "training.wrong", {
                answer: answer.replace(/\.$/, ""),
              })}
            </strong>
          </p>
          <p className="text-sm leading-5 text-fg-secondary">{question.why}</p>
        </div>
      )}
      <div className="mt-auto">
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={!picked}
          isPending={complete.isPending}
          onPress={() => (last ? finish() : setQ(q + 1))}
        >
          {t(last ? "training.finish" : "training.next")}
        </Button>
      </div>
    </>,
  );
}
