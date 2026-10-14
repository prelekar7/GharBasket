import { BagLabel, Button, EmptyState, ProgressBar, Skeleton, toast } from "@gharbasket/ui";
import { CheckIcon, MinusIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { CodeSheet } from "@/features/packing/CodeSheet.jsx";
import { qtyText } from "@/features/packing/format.js";
import { usePackingActions, usePackingDay } from "@/features/packing/queries.js";
import { useOpenBag } from "@/features/packing/useOpenBag.js";
import { normalizeCode } from "@/lib/scan.js";

/** @typedef {import("@/features/packing/queries.js").PackingDay} PackingDay */
/** @typedef {import("@/features/packing/queries.js").PackingTask} PackingTask */
/** @typedef {import("@/features/packing/queries.js").PackLine} PackLine */

/** Pick and pack one bag by scanning each item (designs Packer · Pick and pack, Wrong item scanned; M6 US-5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer/pick")({
  validateSearch: (/** @type {Record<string, unknown>} */ s) => ({
    bag: typeof s.bag === "string" ? s.bag : undefined,
  }),
  component: Pick,
});

function Pick() {
  const { bag } = Route.useSearch();
  const day = usePackingDay();
  if (day.isPending) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3">
        <Skeleton className="h-5 w-56" />
        <Skeleton className="h-44 w-full" />
        <Skeleton className="h-11 w-full" />
        <Skeleton lines={4} />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }
  if (!day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const { tasks } = day.data;
  // From the queue the bag comes in the link; from the tab it is the bag already open.
  const task =
    (bag && tasks.find((x) => x.bag_qr === bag)) ?? tasks.find((x) => x.status === "packing");
  if (!task) return <NoBag tasks={tasks} />;
  return <PickBag key={task.id} task={task} day={day.data} />;
}

/** @param {{ tasks: PackingTask[] }} props */
function NoBag({ tasks }) {
  const { t } = useTranslation();
  const [typing, setTyping] = useState(false);
  const { open, error, next } = useOpenBag(tasks);
  return (
    <div className="flex flex-col gap-3">
      <FieldHeader title={t("packer.pick.title")} />
      <EmptyState title={t("packer.pick.noBag")} body={t("packer.pick.noBagBody")} />
      <ScanField
        label={t("packer.queue.scan")}
        manualLabel={t("packer.queue.manual")}
        expected={next?.bag_qr}
        state={error ? "error" : "idle"}
        errorLabel={error}
        onCode={open}
        onManual={() => setTyping(true)}
      />
      <CodeSheet kind="bag" open={typing} onOpenChange={setTyping} onCode={open} />
    </div>
  );
}

/** @param {{ task: PackingTask, day: PackingDay }} props */
function PickBag({ task, day }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actions = usePackingActions();
  const [scan, setScan] = useState(
    /** @type {{ state: "idle" | "success" | "error", label?: string, wrong?: string }} */ ({
      state: "idle",
    }),
  );
  const [typing, setTyping] = useState(false);
  const [all, setAll] = useState(false);
  const society = day.societies.find((s) => s.id === task.society_id);
  const lines = task.lines;
  const done = lines.filter((l) => l.status !== "todo");
  const next = lines.find((l) => l.status === "todo");
  const packed = task.status === "packed";
  const product = (/** @type {string} */ code) =>
    day.products[code] ?? { name: code, unit: "pc", shelf: "" };
  const qty = (/** @type {PackLine} */ l) => qtyText(t, l.qty, product(l.product_code).unit);
  const item = (/** @type {PackLine} */ l) => `${product(l.product_code).name} · ${qty(l)}`;

  const onCode = (/** @type {string} */ raw) => {
    const code = normalizeCode(raw);
    const line = lines.find((l) => l.product_code === code && l.status === "todo");
    if (line) {
      actions.pick(task, code);
      setScan({
        state: "success",
        label: t("packer.pick.added", { item: product(code).name, qty: qty(line) }),
      });
      return;
    }
    const known = day.products[code];
    if (!known) return setScan({ state: "error", label: t("packer.pick.unknown", { code }) });
    if (lines.some((l) => l.product_code === code))
      return setScan({ state: "error", label: t("packer.pick.already", { item: known.name }) });
    setScan({
      state: "error",
      label: t("packer.pick.wrong", { item: known.name }),
      wrong: known.name,
    });
  };
  const markShort = () => {
    if (!next) return;
    actions.markShort(task, next);
    setScan({ state: "idle" });
    toast(t("packer.pick.markedShort", { item: product(next.product_code).name }));
  };
  const close = () => {
    actions.close(task);
    toast(t("packer.pick.closed", { code: task.bag_qr, crate: task.crate }));
    navigate({ to: "/packer" });
  };

  const progress = (
    <ProgressBar
      label={t("packer.pick.lines")}
      value={done.length}
      max={lines.length}
      valueLabel={t("packer.progressValue", { done: done.length, total: lines.length })}
    />
  );
  const scanField = (
    <ScanField
      label={t("packer.pick.scan")}
      manualLabel={t("packer.pick.manual")}
      expected={next?.product_code}
      state={scan.state}
      successLabel={scan.label}
      errorLabel={scan.label}
      onCode={onCode}
      onManual={() => setTyping(true)}
    />
  );
  const sheet = <CodeSheet kind="item" open={typing} onOpenChange={setTyping} onCode={onCode} />;

  // Wrong item scanned: what to do with it, and what this bag needs instead (design PackerScanError).
  if (scan.wrong && next) {
    const p = product(next.product_code);
    return (
      <div className="flex flex-1 flex-col gap-3.5">
        <FieldHeader
          eyebrow={t("packer.pick.where", {
            code: task.bag_qr,
            society: society?.name,
            flat: task.flat,
          })}
          title={t("packer.pick.title")}
        />
        {scanField}
        <section className="flex flex-col gap-1.5 rounded-md bg-danger-soft p-3.5">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">
            {t("packer.pick.putBackTitle")}
          </h2>
          <p className="m-0">{t("packer.pick.putBack", { item: scan.wrong })}</p>
        </section>
        <section className="flex flex-col gap-1.5 rounded-md border border-line-strong bg-surface p-3.5">
          <span className="text-[15px] leading-[22px] text-fg-secondary">
            {t("packer.pick.needs")}
          </span>
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">{item(next)}</h2>
          <span className="text-[15px] leading-[22px] text-fg-secondary">{p.shelf}</span>
        </section>
        {progress}
        <div className="mt-auto">
          <Button variant="secondary" size="lg" block onPress={markShort}>
            {t("packer.pick.isShort", { item: p.name })}
          </Button>
        </div>
        {sheet}
      </div>
    );
  }

  // The two most recent lines done and the next one; the rest on request (design PackerPick).
  const shown = all || packed ? lines : [...done.slice(-2), ...(next ? [next] : [])];
  return (
    <div className="flex flex-col gap-3">
      <h1 className="m-0 text-[15px] leading-[22px] font-semibold text-fg-secondary">
        {t("packer.pick.bag", {
          n: task.route_seq,
          total: day.tasks.length,
          captain: society?.captain,
        })}
      </h1>
      <BagLabel
        code={task.bag_qr}
        flat={task.flat}
        society={society?.name ?? ""}
        stop={society?.stop}
        crate={task.crate}
        lines={lines.length}
        labels={{
          bag: t("packer.pick.label.bag"),
          flat: t("packer.pick.label.flat"),
          stop: t("packer.pick.label.stop", { n: society?.stop }),
          crate: t("packer.pick.label.crate", { crate: task.crate }),
          lines: t("packer.pick.label.lines", { n: lines.length }),
        }}
      />
      {progress}
      <ul className="m-0 list-none p-0">
        {shown.map((l) => (
          <LineRow
            key={l.id}
            name={product(l.product_code).name}
            how={
              l.status === "todo"
                ? l === next
                  ? t("packer.pick.how.next", { qty: qty(l), shelf: product(l.product_code).shelf })
                  : qty(l)
                : t(`packer.pick.how.${l.status}`, { qty: qty(l) })
            }
            status={l.status}
            word={
              l.status === "todo"
                ? l === next
                  ? t("packer.pick.state.next")
                  : ""
                : t(`packer.pick.state.${l.status}`)
            }
          />
        ))}
      </ul>
      {!all && !packed && lines.length > shown.length && (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="-mt-2 min-h-12 cursor-pointer self-start border-0 bg-transparent p-0 text-[15px] font-semibold text-link underline underline-offset-[3px] outline-none focus-visible:shadow-focus"
        >
          {t("packer.pick.showAll", { n: lines.length })}
        </button>
      )}
      {packed ? (
        <p role="status" className="m-0 font-semibold">
          {t("packer.pick.packed", { crate: task.crate })}
        </p>
      ) : next ? (
        scanField
      ) : (
        <p role="status" className="m-0 font-semibold">
          {t("packer.pick.allDone", { crate: task.crate })}
        </p>
      )}
      {!packed && (
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" size="lg" block isDisabled={!next} onPress={markShort}>
            {t("packer.pick.short")}
          </Button>
          <Button variant="primary" size="lg" block isDisabled={!!next} onPress={close}>
            {t("packer.pick.close")}
          </Button>
        </div>
      )}
      {sheet}
    </div>
  );
}

/**
 * One line of the bag: a tick when picked, a dash when short, the word always beside it.
 * @param {{ name: string, how: string, status: PackLine["status"], word: string }} props
 */
function LineRow({ name, how, status, word }) {
  const tick =
    status === "picked"
      ? "border-primary bg-primary text-on-primary"
      : status === "short"
        ? "border-danger bg-danger-soft text-danger"
        : "border-line-strong";
  return (
    <li className="flex items-center gap-3 border-b border-line py-2.5">
      <span
        aria-hidden
        className={`grid size-7 flex-none place-items-center rounded-full border-2 ${tick}`}
      >
        {status === "picked" && <CheckIcon size={16} weight="bold" />}
        {status === "short" && <MinusIcon size={16} weight="bold" />}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <strong>{name}</strong>
        <span className="text-[15px] leading-[22px] text-fg-secondary">{how}</span>
      </span>
      {word && <span className="text-[15px] leading-[22px] text-fg-secondary">{word}</span>}
    </li>
  );
}
