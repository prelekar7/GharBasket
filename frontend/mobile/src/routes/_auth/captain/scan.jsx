import { Button, ProgressBar, QtyStepper, Sheet, Skeleton, TextField, toast } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { bagQr, currentStop, findFlat, isHandedOver } from "@/features/captain/day.js";
import { PhotoButton, ReceivedCard } from "@/features/captain/parts.jsx";
import { useCaptainDay, useHandover } from "@/features/captain/queries.js";
import { Row } from "@/features/kitchen/ui.jsx";
import { clockTime } from "@/features/subscriptions/format.js";
import { ApiError } from "@/lib/api.js";

/**
 * Receiving the van's crates at the gate: count the crates and bags, type the code the rider shows,
 * and send (design CaptainHandover; M7 §3.4). When the counts differ from the van's, scan the bags
 * you have. Crates are counted, not tracked (ADR-0026 §6).
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
  const send = useHandover();
  const [counts, setCounts] = useState(
    /** @type {{ crates: number, bags: number } | null} */ (null),
  );
  const [code, setCode] = useState("");
  const [photoKey, setPhotoKey] = useState(/** @type {string | null} */ (null));
  const [needPhoto, setNeedPhoto] = useState(false);
  const [forceScan, setForceScan] = useState(false);
  const [error, setError] = useState(/** @type {string | null} */ (null));
  const [busy, setBusy] = useState(false);
  const [scanned, setScanned] = useState(/** @type {string[]} */ ([]));
  const [last, setLast] = useState(
    /** @type {{ state: "success" | "error", text: string } | null} */ (null),
  );
  const [manual, setManual] = useState(typing);
  const [typed, setTyped] = useState("");

  if (day.isPending) return <Skeleton lines={8} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const stop = currentStop(d);
  if (!stop) return <ScreenError onRetry={() => day.refetch()} />;
  const done = isHandedOver(d);
  const crates = counts?.crates ?? stop.expected.crates;
  const bags = counts?.bags ?? stop.expected.bags;
  const differs = crates !== stop.expected.crates || bags !== stop.expected.bags;
  const scanning = differs || forceScan;
  const onVan = d.flats.filter((f) => f.status === "on_the_way");
  const next = onVan.find((f) => !scanned.includes(f.order_id));
  const ready = (photoKey || /^\d{4}$/.test(code.trim())) && (!scanning || scanned.length > 0);

  const take = (/** @type {string} */ raw) => {
    const flat = findFlat(onVan, raw);
    const ok = !!flat && !scanned.includes(flat.order_id);
    if (flat && ok) setScanned((s) => [...s, flat.order_id]);
    setLast(
      ok
        ? { state: "success", text: t("captain.handover.scanned", { flat: flat.flat_label }) }
        : { state: "error", text: t("captain.handover.notOurs", { code: raw.trim() }) },
    );
    return ok;
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await send(stop.stop_id, {
        crates,
        bags,
        ...(photoKey ? { photo_key: photoKey } : { handover_code: code.trim() }),
        ...(scanning ? { scanned_bags: scanned.map((id) => bagQr(id)) } : {}),
        client_at: new Date().toISOString(),
      });
      toast(t("captain.handover.sent"));
    } catch (e) {
      const problem = e instanceof ApiError ? e.code : "";
      if (problem === "logistics.count_mismatch") setForceScan(true);
      if (problem === "logistics.code_attempts_exceeded") setNeedPhoto(true);
      if (problem === "logistics.already_handed_over") day.refetch();
      setError(
        {
          "logistics.code_invalid": t("captain.handover.codeWrong"),
          "logistics.code_attempts_exceeded": t("captain.handover.codeLocked"),
          "logistics.count_mismatch": t("captain.handover.mismatch"),
          "logistics.bag_mismatch": t("captain.handover.notOnVan"),
        }[problem] ?? t("captain.handover.failed"),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-1 flex-col gap-3.5">
      <FieldHeader
        eyebrow={t("captain.handover.when", {
          slot: stop.slot.label,
          time: stop.eta_now ? clockTime(stop.eta_now, lang) : stop.slot.start,
        })}
        title={t("captain.handover.title")}
      />
      {done && <ReceivedCard />}
      {!done && (
        <>
          <ul className="m-0 list-none p-0">
            <Row
              tall
              title={t("captain.handover.crates")}
              sub={t("captain.handover.expected", { n: stop.expected.crates })}
              end={
                <QtyStepper
                  label={t("captain.handover.crates")}
                  value={crates}
                  min={0}
                  max={99}
                  onChange={(v) => setCounts({ crates: v, bags })}
                  labels={{
                    decrease: t("kitchen.menu.decrease"),
                    increase: t("kitchen.menu.increase"),
                  }}
                />
              }
            />
            <Row
              tall
              title={t("captain.handover.bags")}
              sub={t("captain.handover.expected", { n: stop.expected.bags })}
              end={
                <QtyStepper
                  label={t("captain.handover.bags")}
                  value={bags}
                  min={0}
                  max={999}
                  onChange={(v) => setCounts({ crates, bags: v })}
                  labels={{
                    decrease: t("kitchen.menu.decrease"),
                    increase: t("kitchen.menu.increase"),
                  }}
                />
              }
            />
          </ul>
          {scanning ? (
            <section className="flex flex-col gap-2">
              <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
                {t("captain.handover.scanHelp")}
              </p>
              <ScanField
                label={t("captain.handover.scan")}
                manualLabel={t("captain.handover.manual")}
                expected={next ? bagQr(next.order_id) : undefined}
                state={last?.state}
                successLabel={last?.text}
                errorLabel={last?.text}
                onCode={take}
                onManual={() => setManual(true)}
              />
              <ProgressBar
                tone="accent"
                label={t("captain.handover.scannedBags")}
                value={scanned.length}
                max={stop.expected.bags}
                valueLabel={t("captain.ofTotal", {
                  done: scanned.length,
                  total: stop.expected.bags,
                })}
              />
            </section>
          ) : (
            <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
              {t("captain.handover.countHelp")}
            </p>
          )}
          <section className="flex flex-col gap-2 rounded-md bg-surface p-3.5">
            <h2 className="m-0 text-[19px] leading-[26px] font-bold">
              {t("captain.handover.codeTitle")}
            </h2>
            <TextField
              label={t("captain.handover.codeLabel")}
              description={t("captain.handover.codeHelp")}
              inputMode="numeric"
              maxLength={4}
              autoComplete="off"
              value={code}
              onChange={setCode}
              isDisabled={!!photoKey}
              isInvalid={!!error}
              errorMessage={error ?? undefined}
            />
            {(needPhoto || photoKey) && (
              <PhotoButton
                added={!!photoKey}
                label={t("captain.handover.photo")}
                doneLabel={t("captain.handover.photoAdded")}
                onKey={setPhotoKey}
              />
            )}
          </section>
        </>
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
              isDisabled={!ready}
              isPending={busy}
              onPress={submit}
            >
              {t("captain.handover.done", { count: crates })}
            </Button>
            {!needPhoto && !photoKey && (
              <Button variant="quiet" block onPress={() => setNeedPhoto(true)}>
                {t("captain.handover.noCode")}
              </Button>
            )}
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
            isDisabled={!typed.trim()}
            onPress={() => {
              if (take(typed)) {
                setManual(false);
                setTyped("");
              }
            }}
          >
            {t("captain.handover.typeConfirm")}
          </Button>
        }
      >
        <TextField
          label={t("captain.handover.typeLabel")}
          placeholder={next?.flat_label ?? "B-402"}
          autoComplete="off"
          value={typed}
          onChange={setTyped}
          isInvalid={last?.state === "error"}
          errorMessage={last?.text}
        />
      </Sheet>
    </div>
  );
}
