import { Button, Chip, formatPaise, MoneyText, QtyStepper, Sheet, Skeleton } from "@gharbasket/ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useProduct } from "@/features/catalog/queries.js";
import { intlLocale } from "@/lib/intlLocale.js";
import { humanize, perUnit, reasonText } from "./format.js";
import { useSubstitutes } from "./queries.js";

/**
 * One box line as a product: its names in our other languages, price per step and per kilo, season,
 * good swaps best first, and how much to bring with Save and Remove (designs HcProduct, Core-B3;
 * M2). Prices are for one step, as the API sends them. Saving, removing and swapping are the caller's job.
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
  const list = new Intl.ListFormat(intlLocale(lang), { type: "conjunction" });
  // The meals a planned line is for; the API names the recipes, not the days.
  const recipes = line?.reason_code === "meal_plan" ? (line.reason_params?.recipes ?? []) : [];
  const p = product.data;
  // The names in our other languages, then the common English name: "भिंडी · भेंडी · Okra".
  const otherNames = p
    ? [...new Set([p.names.hi, p.names.mr, p.names.en])].filter((n) => n !== line?.name).join(" · ")
    : undefined;
  const per = line ? perUnit(line.step, line.unit_price_paise) : null;
  const inSeason = line?.reason_code === "in_season" || p?.tags.includes("in_season");
  const priced = subs.data?.items.flatMap((s) =>
    s.price_paise === null ? [] : [{ ...s, price_paise: s.price_paise }],
  );

  return (
    <Sheet
      open={!!line}
      onOpenChange={(open) => !open && onClose()}
      title={line ? t("box.qty.change", { name: line.name }) : ""}
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
          <p className="text-sm leading-5 text-fg-secondary">
            {t("box.qty.each", { price: formatPaise(line.unit_price_paise), step: step.label })}
            {per &&
              ` · ${t("product.per", { price: formatPaise(per.price_paise), unit: per.unit })}`}
          </p>
          {inSeason && (
            <div>
              <Chip kind="reason">{t("product.season")}</Chip>
            </div>
          )}
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("product.swaps")}
          </span>
          {subs.isError ? (
            <ScreenError onRetry={() => subs.refetch()} />
          ) : !priced ? (
            <Skeleton lines={3} />
          ) : (
            <ul className="-mt-2">
              {priced.map((s) => (
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
                        {s.name} · {s.step.label}
                      </strong>
                      <span className="text-fg-secondary">
                        {reasonText(t, { code: s.reason_code, params: s.reason_params }, lang)}
                      </span>
                    </span>
                    <MoneyText paise={s.price_paise} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {recipes.length > 0 && (
            <p className="text-sm leading-5 text-fg-secondary">
              {t("box.qty.for", { meals: list.format(recipes.map(humanize)) })}
            </p>
          )}
          <div>
            <QtyStepper
              label={t("box.qty.label", { name: line.name })}
              value={qty}
              onChange={setQty}
              min={1}
              max={Math.max(20, line.qty)}
              unit={`× ${step.label}`}
              labels={{ decrease: t("box.qty.less"), increase: t("box.qty.more") }}
            />
          </div>
          <div className="flex items-baseline justify-between gap-3 border-b border-line py-2.5">
            <span className="text-sm leading-5 text-fg-secondary">{t("box.qty.thisLine")}</span>
            <MoneyText paise={qty * line.unit_price_paise} size="lg" />
          </div>
          <Button variant="danger" onPress={onRemove} isDisabled={busy}>
            {t("box.qty.remove", { price: formatPaise(line.total_paise) })}
          </Button>
        </div>
      )}
    </Sheet>
  );
}
