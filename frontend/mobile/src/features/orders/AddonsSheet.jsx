import {
  Badge,
  Button,
  MoneyText,
  QtyStepper,
  Sheet,
  Skeleton,
  TextField,
  toast,
} from "@gharbasket/ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useProductSearch } from "@/features/catalog/queries.js";
import { clockTime, istClock, shortDate, weekdayName } from "@/features/subscriptions/format.js";
import { addonErrorKey, addonsOpen, useSetAddons } from "./addons.js";
import { istDate } from "./format.js";
import { useOrder } from "./queries.js";

const MAX_QTY = 20;

/**
 * The "Add-ons" button on a box that can still take them, with its sheet. Renders nothing once
 * the cut-off has passed or the order is not `approved`.
 * @param {{ order: import("./queries.js").OrderSummary }} props
 */
export function AddonsEntry({ order }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!addonsOpen(order)) return null;
  return (
    <>
      <Button variant="secondary" onPress={() => setOpen(true)}>
        {t("orders.addons.open")}
      </Button>
      <AddonsSheet order={order} open={open} onOpenChange={setOpen} />
    </>
  );
}

/**
 * Add, change or remove add-on lines on an approved box until its cut-off (POST
 * /v1/orders/{id}/addons, M4 §3.1). Add-ons are paid with the box at the cut-off. The sheet keeps
 * a draft and sends only the lines that differ from the order, so Save is off until something changed.
 * @param {{ order: import("./queries.js").OrderSummary, open: boolean, onOpenChange: (open: boolean) => void }} props
 */
export function AddonsSheet({ order, open, onOpenChange }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const detail = useOrder(open ? order.id : undefined, lang);
  const save = useSetAddons();
  const [q, setQ] = useState("");
  const search = useProductSearch(q, lang);
  /** Add-on lines as the order has them, then as the person has them now: product id → line. */
  const [draft, setDraft] = useState(
    /** @type {Record<string, { name: string, label: string, price: number, qty: number }>} */ ({}),
  );
  const version = detail.data?.version;
  // Start from the order's add-on lines each time it is (re)loaded at a new version.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only a new version resets the draft
  useEffect(() => {
    if (!detail.data) return;
    setDraft(
      Object.fromEntries(
        detail.data.lines
          .filter((l) => l.kind === "addon")
          .map((l) => [
            l.product_id,
            { name: l.name, label: l.step.label, price: l.unit_price_paise, qty: l.qty },
          ]),
      ),
    );
  }, [version]);

  const lines = detail.data?.lines ?? [];
  const inBox = new Set(lines.filter((l) => l.kind === "box").map((l) => l.product_id));
  const was = Object.fromEntries(
    lines.filter((l) => l.kind === "addon").map((l) => [l.product_id, l.qty]),
  );
  const changes = Object.entries(draft)
    .filter(([id, l]) => l.qty !== (was[id] ?? 0))
    .map(([product_id, l]) => ({ product_id, qty: l.qty }));
  const cutoff = `${weekdayName(order.cutoff_at, lang)} ${shortDate(istDate(order.cutoff_at), lang)}, ${clockTime(istClock(order.cutoff_at), lang)}`;
  const results = q.trim().length >= 2 ? (search.data ?? []) : [];

  const send = () =>
    save.mutate(
      { orderId: order.id, version: /** @type {number} */ (version), items: changes },
      {
        onSuccess: () => {
          onOpenChange(false);
          toast(t("orders.addons.saved"));
        },
        onError: (e) => {
          const key = addonErrorKey(e);
          toast(t(`orders.addons.${key}`));
          if (key === "closed") onOpenChange(false);
        },
      },
    );

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={t("orders.addons.title")}
      description={t("orders.addons.lead", { cutoff })}
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isPending={save.isPending}
          isDisabled={changes.length === 0 || version === undefined}
          onPress={send}
        >
          {t("orders.addons.save")}
        </Button>
      }
    >
      {detail.isError ? (
        <ScreenError onRetry={() => detail.refetch()} />
      ) : detail.isPending ? (
        <Skeleton lines={3} />
      ) : (
        <div className="flex flex-col gap-3">
          {Object.entries(draft).length === 0 && (
            <p className="text-sm leading-5 text-fg-secondary">{t("orders.addons.none")}</p>
          )}
          <ul>
            {Object.entries(draft).map(([id, l]) => (
              <li
                key={id}
                className="flex items-center justify-between gap-3 border-b border-line py-2.5"
              >
                <span className="flex flex-col text-sm leading-5">
                  <strong>{l.name}</strong>
                  <span className="text-fg-secondary">
                    {l.qty === 0 ? (
                      t("orders.addons.removed")
                    ) : (
                      <>
                        {l.label} · <MoneyText paise={l.price} />
                      </>
                    )}
                  </span>
                </span>
                <QtyStepper
                  label={l.name}
                  value={l.qty}
                  onChange={(qty) => setDraft({ ...draft, [id]: { ...l, qty } })}
                  max={MAX_QTY}
                  labels={{ decrease: t("box.qty.less"), increase: t("box.qty.more") }}
                />
              </li>
            ))}
          </ul>
          <TextField
            label={t("box.add.search")}
            placeholder={t("box.add.placeholder")}
            value={q}
            onChange={setQ}
            type="search"
            autoComplete="off"
          />
          <ul aria-busy={search.isFetching} aria-label={t("box.add.results")} className="-mt-2">
            {results.map((p) => (
              <li
                key={p.product_id}
                className="flex items-center justify-between gap-3 border-b border-line py-2.5"
              >
                <span className="flex flex-col text-sm leading-5">
                  <strong>{p.name}</strong>
                  <span className="text-fg-secondary">
                    {p.step.label}
                    {p.price_paise !== null && (
                      <>
                        {" · "}
                        <MoneyText paise={p.price_paise} />
                      </>
                    )}
                  </span>
                </span>
                {inBox.has(p.product_id) || draft[p.product_id] ? (
                  <Badge tone="outline">
                    {inBox.has(p.product_id) ? t("box.add.inBox") : t("orders.addons.listed")}
                  </Badge>
                ) : (
                  <Button
                    variant="secondary"
                    isDisabled={p.price_paise === null}
                    onPress={() =>
                      setDraft({
                        ...draft,
                        [p.product_id]: {
                          name: p.name,
                          label: p.step.label,
                          price: p.price_paise ?? 0,
                          qty: 1,
                        },
                      })
                    }
                  >
                    {t("box.add.add")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {q.trim().length >= 2 && !search.isFetching && results.length === 0 && (
            <p className="text-sm text-fg-secondary">{t("box.add.none", { q: q.trim() })}</p>
          )}
        </div>
      )}
    </Sheet>
  );
}
