import {
  Button,
  EmptyBasket,
  EmptyState,
  formatPaise,
  ParchiList,
  Sheet,
  Skeleton,
  StatusPill,
  SubstituteSheet,
  toast,
} from "@gharbasket/ui";
import { PlusIcon } from "@phosphor-icons/react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { AddItemSheet } from "@/features/catalog/AddItemSheet.jsx";
import {
  PILL,
  priceSwaps,
  swappedFromPaise,
  useDismissedSwaps,
} from "@/features/subscriptions/cycle.js";
import { showCycleError } from "@/features/subscriptions/errors.js";
import { qtyLabel, reasonText, shortDate, weekdayOf } from "@/features/subscriptions/format.js";
import { ProductSheet } from "@/features/subscriptions/ProductSheet.jsx";
import {
  isEditable,
  undoSwapOps,
  useCurrentCycle,
  useCycleAction,
  useEditCycle,
  useSubstitutes,
} from "@/features/subscriptions/queries.js";
import { markFirstBoxApproved } from "@/lib/install.js";

const FIRST_LINES = 5;

/**
 * Your box as a parchi, with Swap on each line while edits are open; tapping a line opens it as a
 * product with swaps and how much (designs HcBox, HcSubstitute, HcProduct; M3, M2).
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/box")({ component: Box });

function Box() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data: c, isPending, isError, refetch } = useCurrentCycle(lang);
  const approve = useCycleAction("approve");
  const edit = useEditCycle();
  const [changing, setChanging] = useState(/** @type {string | null} */ (null));
  const [adding, setAdding] = useState(false);
  const [all, setAll] = useState(false);
  const [swapping, setSwapping] = useState(
    /** @type {{ id: string, name: string, pricePaise: number } | null} */ (null),
  );
  const subs = useSubstitutes(swapping?.id ?? null, lang);
  const { dismissed, dismiss } = useDismissedSwaps(c?.id);

  if (isPending) return <Skeleton lines={8} />;
  if (isError) return <ScreenError onRetry={() => refetch()} />;
  if (!c)
    return (
      <div className="flex flex-col gap-4">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("box.title")}</h1>
        <EmptyState
          illustration={<EmptyBasket />}
          title={t("week.noCycleTitle")}
          body={t("week.noCycleBody")}
        />
      </div>
    );

  // Swaps stay open after approving, until the cut-off (Core-W3).
  const editable = isEditable(c);
  const alert = priceSwaps(c).find((a) => !dismissed.includes(a.product_id));
  const day = `${t(`days.short.d${weekdayOf(c.delivery_date)}`)} ${shortDate(c.delivery_date, lang)}`;
  const shown = all ? c.lines : c.lines.slice(0, FIRST_LINES);
  const byName = new Map(c.lines.map((l) => [l.name, l]));
  // A substitute the hub has no price for can't be offered as a swap.
  const priced = subs.data?.items.flatMap((s) =>
    s.price_paise === null ? [] : [{ ...s, price_paise: s.price_paise }],
  );

  /** Says why an edit failed; Try again runs it once more. @param {(() => void) | undefined} [retry] */
  const fail = (retry) => (/** @type {unknown} */ e) => showCycleError(t, e, { retry, refetch });

  /** Swap a line for a substitute, with Undo on the toast. */
  const swapTo = (
    /** @type {{ id: string, name: string }} */ from,
    /** @type {import("@/features/subscriptions/queries.js").Substitute} */ sub,
  ) =>
    edit.mutate(
      {
        cycle: c,
        ops: [{ op: "substitute", product_id: from.id, substitute_id: sub.product_id }],
      },
      {
        onSuccess: (after) => {
          // Undo takes the replacement out and adds back what it replaced.
          const now = after.lines.find((l) => l.product_id === sub.product_id);
          toast(t("box.swapped", { from: from.name, to: sub.name }), {
            action: {
              label: t("box.undo"),
              onClick: () =>
                now && edit.mutate({ cycle: after, ops: undoSwapOps(now) }, { onError: fail() }),
            },
          });
        },
        onError: fail(() => swapTo(from, sub)),
      },
    );

  const approveBox = () =>
    approve.mutate(c, {
      onSuccess: () => {
        markFirstBoxApproved();
        toast(t("week.approvedToast", { day }));
      },
      onError: fail(approveBox),
    });

  /** @param {string} productId @param {string} name */
  const addItem = (productId, name) =>
    edit.mutate(
      { cycle: c, ops: [{ op: "add", product_id: productId, qty: 1 }] },
      {
        onSuccess: () => toast(t("box.add.added", { name })),
        onError: fail(() => addItem(productId, name)),
      },
    );

  /** @param {import("@/features/subscriptions/queries.js").BoxLine} line @param {number} qty */
  const saveQty = (line, qty) =>
    edit.mutate(
      { cycle: c, ops: [{ op: "qty", product_id: line.product_id, qty }] },
      {
        onSuccess: () => {
          setChanging(null);
          toast(t("box.qty.saved", { name: line.name }));
        },
        onError: fail(() => saveQty(line, qty)),
      },
    );

  /** Take a line out, with Undo on the toast. @param {import("@/features/subscriptions/queries.js").BoxLine} line */
  const removeLine = (line) =>
    edit.mutate(
      { cycle: c, ops: [{ op: "remove", product_id: line.product_id }] },
      {
        onSuccess: (after) => {
          setChanging(null);
          toast(t("box.qty.removed", { name: line.name }), {
            action: {
              label: t("box.undo"),
              onClick: () =>
                edit.mutate(
                  {
                    cycle: after,
                    ops: [{ op: "add", product_id: line.product_id, qty: line.qty }],
                  },
                  { onError: fail() },
                ),
            },
          });
        },
        onError: fail(() => removeLine(line)),
      },
    );

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-start justify-between gap-2">
        <div className="flex flex-col">
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("box.when", { day })}
          </span>
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            {t("box.title")}
          </h1>
        </div>
        <div className="mt-1.5">
          <StatusPill status={PILL[c.status]}>{t(`week.status.${c.status}`)}</StatusPill>
        </div>
      </header>
      {editable && alert && (
        <PriceAlert
          alert={alert}
          budget={c.budget_lock ? formatPaise(c.budget_lock.budget_paise) : null}
          busy={edit.isPending}
          onKeep={(a) => {
            const line = c.lines.find((l) => l.product_id === a.product_id);
            if (line)
              edit.mutate(
                { cycle: c, ops: undoSwapOps(line) },
                { onSuccess: () => toast(t("box.spike.kept", { item: a.item })), onError: fail() },
              );
          }}
          onOk={(a) => dismiss(a.product_id)}
        />
      )}
      <ParchiList
        title={t("box.parchi")}
        date={day}
        totalPaise={c.total_paise}
        labels={{
          total: t("box.total", { count: c.lines.length }),
          swap: t("box.swap"),
          change: t("box.qty.change"),
          undo: t("box.undo"),
          swapChip: { up: t("box.up"), saves: t("box.saves") },
        }}
        items={shown.map((l) => ({
          name: l.name,
          qty: qtyLabel(l),
          pricePaise: l.total_paise,
          reason: reasonText(t, { code: l.reason_code, params: l.reason_params }, lang),
          swappedFromPaise: swappedFromPaise(l),
        }))}
        onUndo={
          editable
            ? (item) => {
                const line = byName.get(item.name);
                if (line)
                  edit.mutate(
                    { cycle: c, ops: undoSwapOps(line) },
                    {
                      onSuccess: () => toast(t("box.undone", { name: line.name })),
                      onError: fail(),
                    },
                  );
              }
            : undefined
        }
        onItemPress={
          editable
            ? (item) => {
                const line = byName.get(item.name);
                if (line) setChanging(line.product_id);
              }
            : undefined
        }
        onSwap={
          editable
            ? (item) => {
                const line = byName.get(item.name);
                if (line)
                  setSwapping({
                    id: line.product_id,
                    name: line.name,
                    pricePaise: line.unit_price_paise,
                  });
              }
            : undefined
        }
      />
      {editable && (
        <div>
          <Button variant="secondary" onPress={() => setAdding(true)}>
            <PlusIcon aria-hidden size={20} weight="bold" />
            {t("box.add.cta")}
          </Button>
        </div>
      )}
      {/* "+ 9 more items" and the Budget Lock share one row (HcBox). */}
      <div className="flex flex-wrap items-center justify-between gap-x-3">
        {c.lines.length > FIRST_LINES && (
          <Button variant="quiet" className="-ml-4" onPress={() => setAll((v) => !v)}>
            {all ? t("box.less") : t("box.more", { count: c.lines.length - FIRST_LINES })}
          </Button>
        )}
        {c.budget_lock && (
          // Opens Budget Lock settings (HcBudget).
          <Link
            to="/budget"
            className="flex min-h-12 items-center text-sm leading-[22px] text-fg-secondary underline decoration-line-strong underline-offset-3 outline-none focus-visible:shadow-focus"
          >
            {t(c.budget_lock.remaining_paise >= 0 ? "box.within" : "box.over", {
              budget: formatPaise(c.budget_lock.budget_paise),
            })}
          </Link>
        )}
      </div>
      {c.status === "awaiting_approval" && (
        <Button
          variant="primary"
          size="lg"
          block
          isPending={approve.isPending}
          onPress={approveBox}
        >
          {t("week.approve")}
        </Button>
      )}
      {swapping && !priced && (
        <Sheet
          open
          onOpenChange={(open) => !open && setSwapping(null)}
          title={t("box.sheetTitle", { item: swapping.name })}
        >
          {subs.isError ? <ScreenError onRetry={() => subs.refetch()} /> : <Skeleton lines={6} />}
        </Sheet>
      )}
      {swapping && priced && (
        <SubstituteSheet
          open
          onOpenChange={(open) => !open && setSwapping(null)}
          item={{ name: swapping.name, pricePaise: swapping.pricePaise }}
          labels={{
            title: t("box.sheetTitle"),
            note: t("box.sheetNote", {
              item: swapping.name,
              price: formatPaise(swapping.pricePaise),
            }),
            cta: t("box.useThis"),
          }}
          options={priced.map((s) => ({
            name: s.name,
            qty: s.step.label,
            pricePaise: s.price_paise,
            reason: reasonText(t, { code: s.reason_code, params: s.reason_params }, lang),
          }))}
          onConfirm={(picked) => {
            const sub = priced.find(
              (s) => s.name === picked.name && s.price_paise === picked.pricePaise,
            );
            const from = swapping;
            setSwapping(null);
            if (sub && from) swapTo(from, sub);
          }}
        />
      )}
      <AddItemSheet
        open={adding}
        onClose={() => setAdding(false)}
        inBox={new Set(c.lines.map((l) => l.product_id))}
        busyId={
          edit.isPending && edit.variables?.ops[0]?.op === "add"
            ? edit.variables.ops[0].product_id
            : null
        }
        onAdd={addItem}
      />
      <ProductSheet
        line={c.lines.find((l) => l.product_id === changing) ?? null}
        onClose={() => setChanging(null)}
        busy={edit.isPending}
        onSwap={(sub) => {
          const line = c.lines.find((l) => l.product_id === changing);
          setChanging(null);
          if (line) swapTo({ id: line.product_id, name: line.name }, sub);
        }}
        onSave={(qty) => {
          const line = c.lines.find((l) => l.product_id === changing);
          if (line) saveQty(line, qty);
        }}
        onRemove={() => {
          const line = c.lines.find((l) => l.product_id === changing);
          if (line) removeLine(line);
        }}
      />
    </div>
  );
}

/**
 * "Prices moved this week": what Autopilot swapped to stay within the Budget Lock, with Keep and OK
 * (design Core-B4). Built from the box's `price_swap` lines; the API sends no market or meal count.
 * @param {{ alert: import("@/features/subscriptions/cycle.js").PriceSwap, budget: string | null, busy: boolean, onKeep: (a: import("@/features/subscriptions/cycle.js").PriceSwap) => void, onOk: (a: import("@/features/subscriptions/cycle.js").PriceSwap) => void }} props
 */
function PriceAlert({ alert: a, budget, busy, onKeep, onOk }) {
  const { t } = useTranslation();
  const lead =
    a.pct === null
      ? t("box.spike.pricier", { item: a.item })
      : t("box.spike.up", { item: a.item, pct: a.pct });
  return (
    <section
      role="status"
      className="flex flex-col gap-2 rounded-md border-2 border-accent bg-accent-soft px-4 py-3"
    >
      <strong>{t("box.spike.title")}</strong>
      <p className="text-sm leading-5">
        {t(budget ? "box.spike.body" : "box.spike.bodyNoLock", { lead, budget, sub: a.substitute })}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" isDisabled={busy} onPress={() => onKeep(a)}>
          {t("box.spike.keep", { item: a.item, delta: formatPaise(a.keep_delta_paise) })}
        </Button>
        <Button variant="quiet" isDisabled={busy} onPress={() => onOk(a)}>
          {t("box.spike.ok")}
        </Button>
      </div>
    </section>
  );
}
