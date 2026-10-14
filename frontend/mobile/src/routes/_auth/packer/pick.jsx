import { BagLabel, Button, EmptyState, ProgressBar, Skeleton, toast } from "@gharbasket/ui";
import { CheckIcon, MinusIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { CodeSheet } from "@/features/packing/CodeSheet.jsx";
import { bagCode, inBag, lineMatches, lineQty, lineState } from "@/features/packing/lines.js";
import { usePackingActions, usePackingDay, usePackingTask } from "@/features/packing/queries.js";
import { useOpenBag } from "@/features/packing/useOpenBag.js";

/** @typedef {import("@/features/packing/queries.js").PackingDay} PackingDay */
/** @typedef {import("@/features/packing/queries.js").PackingTask} PackingTask */
/** @typedef {import("@/features/packing/queries.js").PackingDetail} PackingDetail */
/** @typedef {import("@/features/packing/queries.js").PackLine} PackLine */

/** Pick and pack one bag by scanning each item (designs Packer · Pick and pack, Wrong item scanned; M6 US-5). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer/pick")({
  validateSearch: (/** @type {Record<string, unknown>} */ s) => ({
    bag: typeof s.bag === "string" ? s.bag : undefined,
  }),
  component: Pick,
});

const PickLoading = () => (
  <div aria-busy="true" className="flex flex-col gap-3">
    <Skeleton className="h-5 w-56" />
    <Skeleton className="h-44 w-full" />
    <Skeleton className="h-11 w-full" />
    <Skeleton lines={4} />
    <Skeleton className="h-16 w-full" />
  </div>
);

function Pick() {
  const { bag } = Route.useSearch();
  const day = usePackingDay();
  // From the queue the bag comes in the link; from the tab it is the bag already open.
  const row = day.data
    ? ((bag && day.data.tasks.find((x) => x.bag_qr === bag)) ??
      day.data.tasks.find((x) => x.status === "picking"))
    : undefined;
  const task = usePackingTask(row?.id);
  if (day.isPending) return <PickLoading />;
  if (!day.data) return <ScreenError onRetry={() => day.refetch()} />;
  if (!row) return <NoBag tasks={day.data.tasks} />;
  if (task.isPending) return <PickLoading />;
  if (!task.data) return <ScreenError onRetry={() => task.refetch()} />;
  return <PickBag key={row.id} task={task.data} day={day.data} />;
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

/** @param {{ task: PackingDetail, day: PackingDay }} props */
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
  const society = task.society.name;
  const code = bagCode(task);
  const lines = task.lines;
  const done = lines.filter((l) => lineState(l) !== "todo");
  const next = lines.find((l) => lineState(l) === "todo");
  const packed = task.status === "packed";
  const item = (/** @type {PackLine} */ l) => `${inBag(l).name} · ${lineQty(l)}`;

  const onCode = (/** @type {string} */ raw) => {
    const line = lines.find((l) => lineState(l) === "todo" && lineMatches(l, raw));
    if (line) {
      actions.pick(task, line);
      setScan({
        state: "success",
        label: t("packer.pick.added", { item: inBag(line).name, qty: lineQty(line) }),
      });
      return;
    }
    const known = lines.find((l) => lineMatches(l, raw));
    if (known)
      return setScan({
        state: "error",
        label: t("packer.pick.already", { item: inBag(known).name }),
      });
    // The API has no list of the hub's products, so a code that is not on this bag is shown as typed.
    const wrong = raw.trim();
    setScan({ state: "error", label: t("packer.pick.wrong", { item: wrong }), wrong });
  };
  const markShort = () => {
    if (!next) return;
    actions.markShort(task, next);
    setScan({ state: "idle" });
    toast(t("packer.pick.markedShort", { item: inBag(next).name }));
  };
  const close = () => {
    actions.close(task);
    toast(t("packer.pick.closed", { code }));
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
      expected={next ? inBag(next).slug : undefined}
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
    return (
      <div className="flex flex-1 flex-col gap-3.5">
        <FieldHeader
          eyebrow={t("packer.pick.where", { code, society, flat: task.flat_label ?? "" })}
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
        </section>
        {progress}
        <div className="mt-auto">
          <Button variant="secondary" size="lg" block onPress={markShort}>
            {t("packer.pick.isShort", { item: inBag(next).name })}
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
        {t("packer.pick.bag", { n: task.seq, total: day.tasks.length, society })}
      </h1>
      <BagLabel
        code={code}
        flat={task.flat_label ?? ""}
        society={society}
        stop={task.route?.stop_seq}
        crate={task.crate_no == null ? undefined : String(task.crate_no)}
        lines={lines.length}
        labels={{
          bag: t("packer.pick.label.bag"),
          flat: t("packer.pick.label.flat"),
          stop: t("packer.pick.label.stop", { n: task.route?.stop_seq }),
          crate: t("packer.pick.label.crate", { crate: task.crate_no }),
          lines: t("packer.pick.label.lines", { n: lines.length }),
        }}
      />
      {progress}
      <ul className="m-0 list-none p-0">
        {shown.map((l) => (
          <LineRow
            key={l.product_id}
            name={inBag(l).name}
            how={
              lineState(l) === "todo"
                ? lineQty(l)
                : t(`packer.pick.how.${lineState(l)}`, { qty: lineQty(l) })
            }
            status={lineState(l)}
            word={
              lineState(l) === "todo"
                ? l === next
                  ? t("packer.pick.state.next")
                  : ""
                : t(`packer.pick.state.${lineState(l)}`)
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
          {task.crate_no == null
            ? t("packer.pick.packedSent")
            : t("packer.pick.packed", { crate: task.crate_no })}
        </p>
      ) : next ? (
        scanField
      ) : (
        <p role="status" className="m-0 font-semibold">
          {t("packer.pick.allDone")}
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
 * @param {{ name: string, how: string, status: ReturnType<typeof lineState>, word: string }} props
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
