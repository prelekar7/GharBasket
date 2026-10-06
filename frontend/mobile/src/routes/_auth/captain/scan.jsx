import {
  Button,
  CrateCounter,
  Dialog,
  ProgressBar,
  Sheet,
  Skeleton,
  TextField,
  toast,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { clock, ReceivedCard, useCrates } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainWrite } from "@/features/captain/queries.js";

/**
 * Receiving the van's crates at the gate: scan each one, show the rider the handover code, then
 * confirm or report a missing crate (design CaptainHandover; M7 US-3). Works without signal.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/scan")({
  validateSearch: (/** @type {Record<string, unknown>} */ s) => ({ typing: s.typing === true }),
  component: Handover,
});

function Handover() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const { typing } = Route.useSearch();
  const day = useCaptainDay();
  const write = useCaptainWrite();
  const { crates, receive, clear } = useCrates(day.data);
  const [last, setLast] = useState(
    /** @type {{ state: "success" | "error", text: string } | null} */ (null),
  );
  const [manual, setManual] = useState(typing);
  const [code, setCode] = useState("");
  const [reporting, setReporting] = useState(false);
  const [bagCode, setBagCode] = useState("");

  if (day.isPending) return <Skeleton lines={8} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const done = !!d.stop.handed_over_at;
  const shown = done ? d.crates : crates;
  const scanned = shown.filter((c) => c.state === "scanned");
  const waiting = shown.filter((c) => c.state === "waiting");
  // A bag reported missing from a crate that did arrive isn't counted.
  const counted =
    scanned.reduce((sum, c) => sum + c.bags, 0) -
    d.bags.filter((b) => b.status === "missing" && scanned.some((c) => c.id === b.crate)).length;
  const missingBag = d.bags.find(
    (b) => b.status === "planned" && b.code === bagCode.trim().toUpperCase(),
  );

  const take = (/** @type {string} */ raw) => {
    const r = receive(raw);
    setLast(
      r.ok
        ? {
            state: "success",
            text: t("captain.handover.scanned", { crate: r.crate.id, count: r.crate.bags }),
          }
        : { state: "error", text: t("captain.handover.notOurs", { crate: r.code }) },
    );
    return r.ok;
  };
  const send = (/** @type {string[]} */ missing, /** @type {string[]} */ missingBags = []) => {
    write({
      path: `/v1/stops/${d.stop.id}/handover`,
      body: {
        crates: scanned.map((c) => c.id),
        missing,
        missing_bags: missingBags,
        bags_count: counted - missingBags.length,
        at: new Date().toISOString(),
      },
    });
    clear();
    toast(t("captain.handover.sent"));
  };

  return (
    <div className="flex flex-1 flex-col gap-3.5">
      <FieldHeader
        eyebrow={t("captain.handover.when", {
          gate: d.stop.gate,
          time: clock(d.stop.handed_over_at ?? new Date().toISOString(), lang),
        })}
        title={t("captain.handover.title")}
      />
      {done ? (
        <ReceivedCard day={d} />
      ) : (
        <ScanField
          label={t("captain.handover.scan")}
          manualLabel={t("captain.manualCrate")}
          expected={waiting[0]?.id}
          state={last?.state}
          successLabel={last?.text}
          errorLabel={last?.text}
          onCode={take}
          onManual={() => setManual(true)}
        />
      )}
      <CrateCounter
        crates={shown}
        labels={{
          label: t("captain.handover.crates"),
          scanned: t("captain.handover.received"),
          waiting: t("captain.handover.waiting"),
          missing: t("captain.handover.missing"),
          bag: t("captain.handover.bagOne", { n: "{n}" }),
          bags: t("captain.handover.bagMany", { n: "{n}" }),
          summary: t("captain.handover.summary", {
            done: "{done}",
            total: "{total}",
            bags: "{bags}",
          }),
        }}
      />
      <ProgressBar
        tone="accent"
        label={t("captain.handover.bags")}
        value={counted}
        max={d.bags.length}
        valueLabel={t("captain.ofTotal", { done: counted, total: d.bags.length })}
      />
      {!done && (
        <section className="flex flex-col gap-2 rounded-md bg-surface p-3.5">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">
            {t("captain.handover.codeTitle", { rider: d.stop.rider_name })}
          </h2>
          <p className="m-0 flex justify-center gap-2.5">
            <span className="sr-only">{d.stop.handover_code.split("").join(" ")}</span>
            {d.stop.handover_code.split("").map((digit, i) => (
              <span
                // biome-ignore lint/suspicious/noArrayIndexKey: the code's digits never reorder
                key={i}
                aria-hidden
                className="grid h-16 w-13 place-items-center rounded-sm border-2 border-role-captain bg-bg text-[32px] font-bold tabular-nums"
              >
                {digit}
              </span>
            ))}
          </p>
          <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
            {t("captain.handover.codeHelp")}
          </p>
        </section>
      )}
      <div className="mt-auto flex flex-col gap-1">
        {done ? (
          <Button
            variant="primary"
            size="lg"
            block
            onPress={() => navigate({ to: "/captain/flats" })}
          >
            {t("captain.received.toFlats")}
          </Button>
        ) : (
          <>
            <Button
              variant="primary"
              size="lg"
              block
              isDisabled={waiting.length > 0}
              onPress={() => send([])}
            >
              {t("captain.handover.done", { count: crates.length })}
            </Button>
            <Button variant="quiet" block onPress={() => setReporting(true)}>
              {t("captain.handover.problem")}
            </Button>
          </>
        )}
      </div>
      <Sheet
        open={manual}
        onOpenChange={setManual}
        title={t("captain.handover.typeTitle")}
        footer={
          <Button
            variant="primary"
            size="lg"
            block
            isDisabled={!code.trim()}
            onPress={() => {
              if (take(code)) {
                setManual(false);
                setCode("");
              }
            }}
          >
            {t("captain.handover.typeConfirm")}
          </Button>
        }
      >
        <TextField
          label={t("captain.handover.typeLabel")}
          placeholder={waiting[0]?.id ?? "S-1"}
          autoComplete="off"
          value={code}
          onChange={setCode}
          isInvalid={last?.state === "error"}
          errorMessage={last?.text}
        />
      </Sheet>
      {waiting.length > 0 ? (
        <Dialog
          isOpen={reporting}
          onOpenChange={setReporting}
          tone="danger"
          title={t("captain.handover.missingTitle", {
            count: waiting.length,
            crates: waiting.map((c) => c.id).join(", "),
          })}
          confirmLabel={t("captain.handover.missingConfirm")}
          cancelLabel={t("captain.handover.missingCancel")}
          onConfirm={() => send(waiting.map((c) => c.id))}
        >
          {t("captain.handover.missingBody", { rider: d.stop.rider_name })}
        </Dialog>
      ) : (
        // Every crate is in, so the missing thing is a bag. A Dialog can't hold back its confirm
        // until the code is a real bag's, so this asks in a sheet, as the crate code above does.
        <Sheet
          open={reporting}
          onOpenChange={setReporting}
          title={t("captain.handover.bagTitle")}
          description={t("captain.handover.missingBody", { rider: d.stop.rider_name })}
          footer={
            <Button
              variant="danger"
              size="lg"
              block
              isDisabled={!missingBag}
              onPress={() => {
                if (!missingBag) return;
                send([], [missingBag.code]);
                setReporting(false);
                setBagCode("");
              }}
            >
              {t("captain.handover.missingConfirm")}
            </Button>
          }
        >
          <TextField
            label={t("captain.handover.bagLabel")}
            placeholder="B-214"
            autoComplete="off"
            value={bagCode}
            onChange={setBagCode}
            isInvalid={!!bagCode.trim() && !missingBag}
            errorMessage={t("captain.handover.bagUnknown", { code: bagCode.trim().toUpperCase() })}
          />
        </Sheet>
      )}
    </div>
  );
}
