import { Button, formatPaise, RadioCards, Sheet, Skeleton, toast } from "@gharbasket/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api.js";
import { timeRange, weekdayName } from "./format.js";
import { slotProblem, useMoveSlot, useSlotQuote } from "./slots.js";

/**
 * "Pick a delivery time": every box slot on the delivery date with what moving there saves (M3
 * §3.6, M10 §3.8). A quote lasts 15 minutes; when it has run out, the sheet asks for a new one and
 * says so, and a different saving is never applied without a second Confirm. Reasons are codes,
 * turned into sentences here. Rendered only when GREEN_SLOTS is on.
 * @param {{ cycle: import("./queries.js").Cycle, open: boolean, onClose: () => void }} props
 */
export function SlotSheet({ cycle, open, onClose }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const client = useQueryClient();
  const quote = useSlotQuote();
  const move = useMoveSlot();
  const [picked, setPicked] = useState(/** @type {string | null} */ (null));
  const { mutate: ask, reset } = quote;
  const q = quote.data;

  useEffect(() => {
    if (!open) return reset();
    setPicked(null);
    ask(/** @type {string} */ (cycle.id));
  }, [open, ask, reset, cycle.id]);

  const items = q?.items ?? [];
  const current = items.find((o) => o.current);
  const selected =
    items.find((o) => o.slot.id === picked && (o.available > 0 || o.current)) ?? current;
  const day = weekdayName(cycle.delivery_date, lang);

  /** Says what went wrong in plain words, then asks for a fresh quote, reloads the week or closes. @param {unknown} e */
  const refused = (e) => {
    const p = slotProblem(e);
    toast(t(`slots.err.${p.key}`));
    if (p.reload) client.invalidateQueries({ queryKey: ["cycle"] });
    if (p.close) onClose();
    else if (p.requote) ask(/** @type {string} */ (cycle.id));
  };
  const confirm = () => {
    if (!q || !selected) return;
    // The shown saving is only honoured while the quote lives; ask again and show the new numbers.
    if (Date.parse(q.expires_at) <= Date.now())
      return refused(
        new ApiError({ status: 409, code: "subscriptions.quote_expired", title: "Quote expired" }),
      );
    move.mutate(
      { cycle, quoteId: q.id, slotId: selected.slot.id },
      {
        onSuccess: (c) => {
          toast(t("slots.moved", { window: `${day} ${timeRange(c.slot, lang)}` }));
          onClose();
        },
        onError: refused,
      },
    );
  };

  /** @param {import("./slots.js").SlotOption} o */
  const reason = (o) => {
    const window = `${day} ${timeRange(o.slot, lang)}`;
    if (o.reason_code === "neighbour_stop")
      return o.saving_paise > 0
        ? t("slots.reason.neighbour_stop_save", { window, amount: formatPaise(o.saving_paise) })
        : t("slots.reason.neighbour_stop", { window });
    return o.reason_code === "unpriced" ? "" : t(`slots.reason.${o.reason_code}`);
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={t("slots.title")}
      description={t("slots.lead", { day })}
      footer={
        <>
          <Button variant="quiet" onPress={onClose}>
            {t("slots.stay")}
          </Button>
          <Button
            variant="primary"
            size="lg"
            isDisabled={!selected || selected.current}
            isPending={move.isPending}
            onPress={confirm}
          >
            {t("slots.confirm")}
          </Button>
        </>
      }
    >
      {quote.isError ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm leading-5">{t(`slots.err.${slotProblem(quote.error).key}`)}</p>
          <Button variant="secondary" onPress={() => ask(/** @type {string} */ (cycle.id))}>
            {t("onboarding.retry")}
          </Button>
        </div>
      ) : !q || quote.isPending ? (
        <Skeleton lines={4} />
      ) : (
        <>
          {q.source === "unpriced" && (
            <p className="text-sm leading-5 text-fg-secondary">{t("slots.unpriced")}</p>
          )}
          <RadioCards
            label={t("slots.title")}
            value={selected?.slot.id ?? ""}
            onChange={setPicked}
            options={items.map((o) => ({
              value: o.slot.id,
              title: timeRange(o.slot, lang),
              description: !o.current && o.available === 0 ? t("slots.full") : reason(o),
              badge: o.current ? t("slots.current") : o.green ? t("slots.green") : undefined,
              meta:
                !o.current && o.saving_paise > 0
                  ? t("slots.save", { amount: formatPaise(o.saving_paise) })
                  : undefined,
              disabled: !o.current && o.available === 0,
            }))}
          />
        </>
      )}
    </Sheet>
  );
}
