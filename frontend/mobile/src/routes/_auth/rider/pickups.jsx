import { Button, Checkbox, EmptyState, Sheet, Skeleton, TextField } from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useMyRoute, useRiderActions } from "@/features/logistics/queries.js";
import { clockTime } from "@/features/subscriptions/format.js";
import { normalizeCode } from "@/lib/scan.js";

/** Empty crates and donation bags collected on the way back to the hub (design RiderPickups; M7 §3.6, an outline). */
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

  const donations = route.stops.flatMap((stop) =>
    (stop.pickups?.donations ?? []).map((donation) => ({ stop, donation })),
  );
  const pending = donations.filter((d) => d.donation.status === "expected");
  /** Collects the donation a scanned or typed code belongs to; false when it is not on this route. @param {string} raw */
  const collectCode = (raw) => {
    const typed = normalizeCode(raw);
    const hit = pending.find((d) => d.donation.ref === typed);
    if (hit)
      actions.pickups(route, hit.stop, {
        donations: [{ donation_id: hit.donation.donation_id, status: "collected" }],
      });
    setScan(hit ? "success" : "error");
    setTimeout(() => setScan("idle"), 2500);
    return !!hit;
  };
  const places = route.stops.filter(
    (s) => s.empties_expected > 0 || (s.pickups?.donations.length ?? 0) > 0,
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
              label={t("rider.pickups.donation", { ref: d.ref })}
              description={d.note}
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
        </section>
      ))}
      {pending.length > 0 && (
        <ScanField
          label={t("rider.pickups.scan")}
          manualLabel={t("rider.pickups.manual")}
          expected={pending[0]?.donation.ref}
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
