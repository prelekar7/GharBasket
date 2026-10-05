import { Button, IconButton, ProgressBar, RadioCards, toast } from "@gharbasket/ui";
import { CheckCircleIcon, WarningCircleIcon, XIcon } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useCompleteTraining } from "@/features/partners/queries.js";

/**
 * One training module as a quiz: one question at a time, with the reason after each answer
 * (design HcPartnerQuiz, M8 US-3). Used for applicants and for verified partners' refreshers; the
 * screen around it supplies the page (side padding of 20px) and decides where "close" and a pass
 * go. Give it `key={module.code}` so a new module starts clean.
 * @param {{ module: import("@/features/partners/queries.js").TrainingModule, position: { n: number, total: number }, onClose: () => void, onPassed?: () => void }} props
 */
export function TrainingQuiz({ module: m, position, onClose, onPassed }) {
  const { t } = useTranslation();
  const complete = useCompleteTraining();
  const [q, setQ] = useState(0);
  const [answers, setAnswers] = useState(/** @type {Record<string, string>} */ ({}));
  const [failed, setFailed] = useState(
    /** @type {{ score: number, total: number } | null} */ (null),
  );
  // One key per try at a module, so a retry after a dropped connection is not scored twice.
  const attempt = useRef(crypto.randomUUID());
  const restart = () => {
    setQ(0);
    setAnswers({});
    setFailed(null);
    attempt.current = crypto.randomUUID();
  };

  const header = (
    <header className="-ml-3 flex items-center gap-1 py-2">
      <IconButton label={t("training.close")} variant="quiet" onPress={onClose}>
        <XIcon size={24} />
      </IconButton>
      <div className="flex flex-col">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("training.eyebrow", position)}
        </span>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{m.title}</h1>
      </div>
    </header>
  );

  if (failed)
    return (
      <>
        {header}
        <p role="status" className="rounded-md bg-danger-soft px-3.5 py-3 font-semibold">
          {t("training.failed", failed)}
        </p>
        <div className="mt-auto">
          <Button variant="primary" size="lg" block onPress={restart}>
            {t("training.retry")}
          </Button>
        </div>
      </>
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
            onPassed?.();
          } else setFailed(r);
        },
        onError: () => toast(t("training.saveFailed")),
      },
    );

  return (
    <>
      {header}
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
    </>
  );
}
