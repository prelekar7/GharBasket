import { Button, SegmentedControl, Sheet, Skeleton, toast } from "@gharbasket/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useOrder } from "@/features/orders/queries.js";
import {
  feedbackErrorKey,
  feedbackSent,
  markFeedbackSent,
  SIGNALS,
  useSendFeedback,
} from "./feedback.js";

/**
 * The "How was the food?" button on a delivered box, with its sheet; once sent, a thank-you line
 * (remembered on this phone; the API itself takes later answers over earlier ones).
 * @param {{ order: { id: string, kind: string, cycle_id: string | null } }} props
 */
export function FeedbackEntry({ order }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [sent, setSent] = useState(() => !!order.cycle_id && feedbackSent(order.cycle_id));
  if (order.kind !== "box" || !order.cycle_id) return null;
  if (sent)
    return (
      <p className="w-full text-sm leading-5 text-fg-secondary">{t("orders.feedback.thanks")}</p>
    );
  return (
    <>
      <Button variant="secondary" onPress={() => setOpen(true)}>
        {t("orders.feedback.open")}
      </Button>
      <FeedbackSheet
        order={order}
        open={open}
        onOpenChange={setOpen}
        onSent={() => {
          markFeedbackSent(/** @type {string} */ (order.cycle_id));
          setSent(true);
        }}
      />
    </>
  );
}

/**
 * After a delivery: for each item of the box, did it run out, get wasted, or was it just right
 * (POST /v1/cycles/{id}/feedback, M3 §3.1). It feeds the pantry twin so next week's box is sized
 * better. Only items of the box itself count: add-ons and packing substitutes are not on the cycle.
 * @param {{ order: { id: string, cycle_id: string | null }, open: boolean, onOpenChange: (open: boolean) => void, onSent: () => void }} props
 */
export function FeedbackSheet({ order, open, onOpenChange, onSent }) {
  const { t, i18n } = useTranslation();
  const detail = useOrder(open ? order.id : undefined, i18n.resolvedLanguage ?? "en-IN");
  const send = useSendFeedback();
  const [answers, setAnswers] = useState(/** @type {Record<string, string>} */ ({}));
  const items = (detail.data?.lines ?? []).filter((l) => l.kind === "box" && !l.substituted_from);
  const given = items.filter((l) => answers[l.product_id]);

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={t("orders.feedback.title")}
      description={t("orders.feedback.lead")}
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isPending={send.isPending}
          isDisabled={given.length === 0 || !order.cycle_id}
          onPress={() =>
            send.mutate(
              {
                cycleId: /** @type {string} */ (order.cycle_id),
                items: given.map((l) => ({
                  product_id: l.product_id,
                  signal: /** @type {import("./feedback.js").Signal} */ (answers[l.product_id]),
                })),
              },
              {
                onSuccess: () => {
                  onSent();
                  onOpenChange(false);
                  toast(t("orders.feedback.sent"));
                },
                onError: (e) => {
                  const key = feedbackErrorKey(e);
                  toast(t(`orders.feedback.${key}`));
                  if (key !== "failed") onOpenChange(false);
                },
              },
            )
          }
        >
          {t("orders.feedback.send")}
        </Button>
      }
    >
      {detail.isError ? (
        <ScreenError onRetry={() => detail.refetch()} />
      ) : detail.isPending ? (
        <Skeleton lines={4} />
      ) : (
        <ul>
          {items.map((l) => (
            <li key={l.product_id} className="flex flex-col gap-2 border-b border-line py-3">
              <strong className="text-sm leading-5">
                {l.name} · {l.step.label}
              </strong>
              <SegmentedControl
                label={l.name}
                value={answers[l.product_id] ?? ""}
                onChange={(signal) => setAnswers({ ...answers, [l.product_id]: signal })}
                options={SIGNALS.map((value) => ({ value, label: t(`orders.feedback.${value}`) }))}
              />
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}
