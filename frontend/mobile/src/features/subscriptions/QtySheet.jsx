import { Button, formatPaise, MoneyText, QtyStepper, Sheet } from "@gharbasket/ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { weekdayOf } from "./format.js";

/**
 * How much of one box line to bring: price per step, which meals it is for, a stepper, Remove and
 * Save (design Core-B3). Saving or removing is the caller's job.
 * @param {{ line: import("./queries.js").BoxLine | null, onClose: () => void, onSave: (qty: number) => void, onRemove: () => void, busy: boolean }} props
 */
export function QtySheet({ line, onClose, onSave, onRemove, busy }) {
  const { t, i18n } = useTranslation();
  const [qty, setQty] = useState(line?.qty ?? 1);
  useEffect(() => setQty(line?.qty ?? 1), [line]);
  const step = line?.step;
  const list = new Intl.ListFormat(i18n.resolvedLanguage ?? "en-IN", { type: "conjunction" });
  const uses = (line?.used_in ?? []).map((u) =>
    t("box.qty.use", { day: t(`days.short.d${weekdayOf(u.date)}`), dish: u.dish }),
  );

  return (
    <Sheet
      open={!!line}
      onOpenChange={(open) => !open && onClose()}
      title={line?.name ?? ""}
      description={
        step
          ? t("box.qty.each", { price: formatPaise(step.price_paise), step: step.label })
          : undefined
      }
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isPending={busy}
          isDisabled={qty === line?.qty}
          onPress={() => onSave(qty)}
        >
          {t("box.qty.save")}
        </Button>
      }
    >
      {line && step && (
        <div className="flex flex-col gap-4">
          {uses.length > 0 && (
            <p className="text-sm leading-5 text-fg-secondary">
              {t("box.qty.for", { meals: list.format(uses) })}
            </p>
          )}
          <div className="flex items-center justify-between gap-3">
            <QtyStepper
              label={t("box.qty.label", { name: line.name })}
              value={qty}
              onChange={setQty}
              min={1}
              max={20}
              unit={`× ${step.label}`}
              labels={{ decrease: t("box.qty.less"), increase: t("box.qty.more") }}
            />
            <MoneyText paise={qty * step.price_paise} size="lg" />
          </div>
          <Button variant="danger" onPress={onRemove} isDisabled={busy}>
            {t("box.qty.remove", { price: formatPaise(line.price_paise) })}
          </Button>
        </div>
      )}
    </Sheet>
  );
}
