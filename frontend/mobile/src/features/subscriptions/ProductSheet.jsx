import { Button, Chip, formatPaise, MoneyText, QtyStepper, Sheet, Skeleton } from "@gharbasket/ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useProduct } from "@/features/catalog/queries.js";
import { reasonText, weekdayOf } from "./format.js";
import { useSubstitutes } from "./queries.js";

/**
 * One box line as a product: its names in our other languages, list price, season, good swaps
 * best first, and how much to bring with Save and Remove (designs HcProduct, Core-B3; M2). Saving,
 * removing and swapping are the caller's job.
 * @param {{ line: import("./queries.js").BoxLine | null, onClose: () => void, onSave: (qty: number) => void, onRemove: () => void, onSwap: (sub: import("./queries.js").Substitute) => void, busy: boolean }} props
 */
export function ProductSheet({ line, onClose, onSave, onRemove, onSwap, busy }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const [qty, setQty] = useState(line?.qty ?? 1);
  useEffect(() => setQty(line?.qty ?? 1), [line]);
  const id = line?.product_id ?? null;
  const product = useProduct(id, lang);
  const subs = useSubstitutes(id, lang);
  const step = line?.step;
  const list = new Intl.ListFormat(lang, { type: "conjunction" });
  const uses = (line?.used_in ?? []).map((u) =>
    t("box.qty.use", { day: t(`days.short.d${weekdayOf(u.date)}`), dish: u.dish }),
  );
  const p = product.data;
  // The names in our other languages, then the English common name: "भिंडी · भेंडी · Okra".
  const otherNames = p
    ? [...new Set([p.names.hi, p.names.mr, p.names.en, ...p.also_called])]
        .filter((n) => n !== line?.name)
        .join(" · ")
    : undefined;

  return (
    <Sheet
      open={!!line}
      onOpenChange={(open) => !open && onClose()}
      title={line?.name ?? ""}
      description={otherNames}
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
        <div className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-sm leading-5 text-fg-secondary">
              {qty === line.qty ? line.qty_label : `${qty} × ${step.label}`}
              {p &&
                ` · ${t("product.per", { price: formatPaise(p.unit_price.price_paise), unit: p.unit_price.unit })}`}
            </span>
            <MoneyText paise={qty * step.price_paise} size="lg" />
          </div>
          {product.isPending && <Skeleton className="h-6 w-40" />}
          {p?.tags.includes("in_season") && (
            <div>
              <Chip kind="reason">{t("product.season")}</Chip>
            </div>
          )}
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("product.swaps")}
          </span>
          {product.isError || subs.isError ? (
            <ScreenError
              onRetry={() => {
                product.refetch();
                subs.refetch();
              }}
            />
          ) : !subs.data ? (
            <Skeleton lines={3} />
          ) : (
            <ul className="-mt-2">
              {subs.data.items.map((s) => (
                <li key={s.product_id} className="border-b border-line">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onSwap(s)}
                    className="flex min-h-12 w-full items-baseline justify-between gap-3 py-2.5 text-left text-sm leading-5 outline-none focus-visible:shadow-focus disabled:opacity-60"
                  >
                    <span className="flex flex-col">
                      <span className="sr-only">{t("box.useThis", { name: s.name })}:</span>
                      <strong>
                        {s.name} · {s.qty_label}
                      </strong>
                      {s.reason_code && (
                        <span className="text-fg-secondary">
                          {reasonText(t, { code: s.reason_code, params: s.reason_params })}
                        </span>
                      )}
                    </span>
                    <MoneyText paise={s.price_paise} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {uses.length > 0 && (
            <p className="text-sm leading-5 text-fg-secondary">
              {t("box.qty.for", { meals: list.format(uses) })}
            </p>
          )}
          <div>
            <QtyStepper
              label={t("box.qty.label", { name: line.name })}
              value={qty}
              onChange={setQty}
              min={1}
              max={20}
              unit={`× ${step.label}`}
              labels={{ decrease: t("box.qty.less"), increase: t("box.qty.more") }}
            />
          </div>
          <Button variant="danger" onPress={onRemove} isDisabled={busy}>
            {t("box.qty.remove", { price: formatPaise(line.price_paise) })}
          </Button>
        </div>
      )}
    </Sheet>
  );
}
