import { Button, IconButton, ProgressBar, RadioCards, toast } from "@gharbasket/ui";
import { XIcon } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useCompleteTraining } from "@/features/partners/queries.js";
import { ApiError } from "@/lib/api.js";

/**
 * One training module as a quiz: one question at a time; the server scores all the answers at the
 * end, because it never sends the right ones (design HcPartnerQuiz, M8 §3.2). Used for applicants and for verified partners' refreshers; the
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
    /** @type {{ correct: number, total: number } | null} */ (null),
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
          {t("training.failed", {
            score: failed.correct,
            total: failed.total,
            pass: Math.round(m.pass_score * 100),
          })}
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
  const last = q === m.questions.length - 1;
  const finish = () =>
    complete.mutate(
      { code: m.code, version: m.version, answers, key: attempt.current },
      {
        onSuccess: (r) => {
          if (r.passed) {
            restart();
            toast(t("training.passed", { score: r.correct, total: r.total }));
            onPassed?.();
          } else {
            attempt.current = crypto.randomUUID();
            setFailed(r);
          }
        },
        onError: (e) => {
          // A newer version reloads the module (the screen swaps it in); five tries a day is a hard stop.
          if (e instanceof ApiError && e.code === "partners.training_version_stale") restart();
          toast(
            t(
              e instanceof ApiError && e.status === 429
                ? "training.tooMany"
                : e instanceof ApiError && e.code === "partners.training_version_stale"
                  ? "training.updated"
                  : "training.saveFailed",
            ),
          );
        },
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
        onChange={(v) => setAnswers((a) => ({ ...a, [question.id]: v }))}
        options={question.options}
      />
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
