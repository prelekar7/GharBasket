import { Button, Checkbox, EmptyState, Sheet, Skeleton, TextField } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useMyRoute, useRiderActions } from "@/features/logistics/queries.js";
import { clockTime } from "@/features/subscriptions/format.js";
import { bagQr, parseBagQr } from "@/lib/scan.js";

/**
 * Empty crates, donations and returned bags collected on the way back to the hub (design RiderPickups;
 * M7 §3.6). Empties and donations are tapped; a donation has no label or code, so the rider tells
 * it apart by its items (and the flat, in rider mode). A returned bag is scanned: its `GB1:` QR goes
 * in `bags`.
 */
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
  const route = query.data.route;
  if (!route) return <EmptyState title={t("rider.none.title")} />;

  const bags = route.stops.flatMap((stop) =>
    (stop.pickups?.bags ?? []).map((bag) => ({ stop, bag, qr: bagQr(bag.order_id) })),
  );
  const pending = bags.filter((b) => b.bag.status === "expected");
  /** Collects the returned bag a scanned or typed `GB1:` code belongs to; false when it is not on this route. @param {string} raw */
  const collectCode = (raw) => {
    const qr = parseBagQr(raw);
    const hit = qr ? pending.find((b) => b.qr === qr) : undefined;
    if (hit) actions.pickups(route, hit.stop, { bags: [hit.qr] });
    setScan(hit ? "success" : "error");
    setTimeout(() => setScan("idle"), 2500);
    return !!hit;
  };
  const places = route.stops.filter(
    (s) =>
      s.empties_expected > 0 ||
      (s.pickups?.donations.length ?? 0) > 0 ||
      (s.pickups?.bags?.length ?? 0) > 0,
  );

  return (
    <div className="flex flex-1 flex-col gap-3">
      <FieldHeader
        eyebrow={t("rider.pickups.when", { time: clockTime(route.return_by, lang) })}
        title={t("rider.pickups.title")}
      />
      {places.length === 0 && (
        <p className="text-[15px] text-fg-secondary">{t("rider.pickups.none")}</p>
      )}
      {places.map((stop) => (
        <section key={stop.id} className="flex flex-col gap-1 rounded-md bg-surface px-3.5 py-3">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">{stop.society?.name}</h2>
          {stop.empties_expected > 0 && (
            <Checkbox
              label={t("rider.pickups.crates", { n: stop.empties_expected })}
              isSelected={stop.pickups?.empties_collected != null}
              isReadOnly={stop.pickups?.empties_collected != null}
              onChange={(on) =>
                on && actions.pickups(route, stop, { empties: stop.empties_expected })
              }
            />
          )}
          {stop.pickups?.donations.map((d) => (
            <Checkbox
              key={d.donation_id}
              label={
                d.items
                  ? t("rider.pickups.donation", { items: d.items })
                  : t("rider.pickups.donationBag")
              }
              description={d.flat_label ?? undefined}
              isSelected={d.status === "collected"}
              isReadOnly={d.status === "collected"}
              onChange={(on) =>
                on &&
                actions.pickups(route, stop, {
                  donations: [{ donation_id: d.donation_id, status: "collected" }],
                })
              }
            />
          ))}
          {stop.pickups?.bags?.map((b) => (
            <Checkbox
              key={b.order_id}
              label={t("rider.pickups.bag")}
              description={b.flat_label ?? undefined}
              isSelected={b.status === "collected"}
              isReadOnly
            />
          ))}
        </section>
      ))}
      {pending.length > 0 && (
        <ScanField
          label={t("rider.pickups.scan")}
          manualLabel={t("rider.pickups.manual")}
          expected={pending[0]?.qr}
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
