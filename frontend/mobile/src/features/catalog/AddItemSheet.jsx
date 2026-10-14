import { Button, MoneyText, Sheet, TextField } from "@gharbasket/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useProductSearch } from "./queries.js";

/**
 * Add something to this week's box: search in English, Hindi or Hinglish and add one step.
 * Add-ons are lines on the weekly box, paid at the same cut-off (design review, 2 Oct; M2 search).
 * @param {{ open: boolean, onClose: () => void, inBox: Set<string>, onAdd: (productId: string, name: string) => void, busyId: string | null }} props
 */
export function AddItemSheet({ open, onClose, inBox, onAdd, busyId }) {
  const { t, i18n } = useTranslation();
  const [q, setQ] = useState("");
  const { data: items, isFetching } = useProductSearch(q, i18n.resolvedLanguage ?? "en-IN");
  const shown = q.trim().length >= 2 ? (items ?? []) : [];

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={t("box.add.title")}
      description={t("box.add.lead")}
    >
      <div className="flex flex-col gap-3">
        <TextField
          label={t("box.add.search")}
          placeholder={t("box.add.placeholder")}
          value={q}
          onChange={setQ}
          type="search"
          autoComplete="off"
        />
        <ul aria-busy={isFetching} aria-label={t("box.add.results")}>
          {shown.map((p) => (
            <li
              key={p.product_id}
              className="flex items-center justify-between gap-3 border-b border-line py-2.5"
            >
              <span className="flex flex-col">
                <span className="font-semibold">{p.name}</span>
                <span className="text-sm text-fg-secondary">
                  {p.step} · <MoneyText paise={p.price_paise} />
                </span>
              </span>
              {inBox.has(p.product_id) ? (
                <span className="text-sm text-fg-secondary">{t("box.add.inBox")}</span>
              ) : (
                <Button
                  variant="secondary"
                  isPending={busyId === p.product_id}
                  onPress={() => onAdd(p.product_id, p.name)}
                >
                  {t("box.add.add")}
                </Button>
              )}
            </li>
          ))}
        </ul>
        {q.trim().length >= 2 && !isFetching && shown.length === 0 && (
          <p className="text-sm text-fg-secondary">{t("box.add.none", { q: q.trim() })}</p>
        )}
      </div>
    </Sheet>
  );
}
