import { Button, Checkbox, EmptyState, Sheet, Skeleton, TextField } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useMyRoute, useRiderActions } from "@/features/logistics/queries.js";
import { timeRange } from "@/features/subscriptions/format.js";
import { normalizeCode } from "@/lib/scan.js";

/** Empty crates and donation bags collected on the way back to the hub (design RiderPickups; M7 US-7). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/rider/pickups")({ component: RiderPickups });

function RiderPickups() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const query = useMyRoute(lang);
  const actions = useRiderActions();
  const [scan, setScan] = useState(/** @type {"idle" | "success" | "error"} */ ("idle"));
  const [typing, setTyping] = useState(false);
  const [code, setCode] = useState("");
  const [wrong, setWrong] = useState(false);

  if (query.isPending) return <Skeleton lines={8} />;
  if (!query.data) return <ScreenError onRetry={() => query.refetch()} />;
  const route = query.data.items[0];
  if (!route) return <EmptyState title={t("rider.none.title")} />;

  const pending = route.pickups.filter((p) => p.status === "pending");
  /** Collects the pickup a scanned or typed code belongs to; false when it is not on this route. @param {string} raw */
  const collectCode = (raw) => {
    const typed = normalizeCode(raw);
    const pickup = pending.find((p) => p.codes.includes(typed));
    if (pickup) actions.collect(route, pickup, typed);
    setScan(pickup ? "success" : "error");
    setTimeout(() => setScan("idle"), 2500);
    return !!pickup;
  };
  const places = route.stops
    .map((s) => ({ stop: s, items: route.pickups.filter((p) => p.stop_id === s.id) }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="flex flex-1 flex-col gap-3">
      <FieldHeader
        eyebrow={t("rider.pickups.when", { window: timeRange(route.back, lang) })}
        title={t("rider.pickups.title")}
      />
      {places.length === 0 && (
        <p className="text-[15px] text-fg-secondary">{t("rider.pickups.none")}</p>
      )}
      {places.map(({ stop, items }) => (
        <section key={stop.id} className="flex flex-col gap-1 rounded-md bg-surface px-3.5 py-3">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">{stop.society.name}</h2>
          {items.map((p) => (
            <Checkbox
              key={p.id}
              label={
                p.kind === "crate"
                  ? t("rider.pickups.crates", { n: p.qty })
                  : t("rider.pickups.donation", { ref: p.ref })
              }
              description={p.note}
              isSelected={p.status === "collected"}
              isReadOnly={p.status === "collected"}
              onChange={(on) => on && actions.collect(route, p)}
            />
          ))}
        </section>
      ))}
      {pending.length > 0 && (
        <ScanField
          label={t("rider.pickups.scan")}
          manualLabel={t("rider.pickups.manual")}
          expected={pending[0]?.codes[0]}
          state={scan}
          successLabel={t("rider.pickups.scanned")}
          errorLabel={t("rider.pickups.unknown")}
          onCode={collectCode}
          onManual={() => {
            setCode("");
            setWrong(false);
            setTyping(true);
          }}
        />
      )}
      <p className="text-[15px] leading-[22px] text-fg-secondary">{t("rider.pickups.why")}</p>
      <Sheet
        open={typing}
        onOpenChange={setTyping}
        title={t("rider.pickups.typeTitle")}
        footer={
          <Button
            variant="primary"
            size="lg"
            isDisabled={!code.trim()}
            onPress={() => {
              if (collectCode(code)) setTyping(false);
              else setWrong(true);
            }}
          >
            {t("rider.pickups.collect")}
          </Button>
        }
      >
        <TextField
          label={t("rider.pickups.codeLabel")}
          description={t("rider.pickups.codeHelp")}
          value={code}
          onChange={(v) => {
            setCode(v);
            setWrong(false);
          }}
          isInvalid={wrong}
          errorMessage={t("rider.pickups.unknown")}
        />
      </Sheet>
    </div>
  );
}
