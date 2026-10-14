import { Button, FlatChecklist, ProgressBar, SegmentedControl, Skeleton } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { nextBag } from "@/features/captain/day.js";
import { dayLabel, isOpen, proof } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainWrite } from "@/features/captain/queries.js";
import { normalizeCode } from "@/lib/scan.js";
import { useSession } from "@/lib/session.js";

/** @typedef {Parameters<typeof import("@gharbasket/ui").StatusPill>[0]["status"]} PillStatus */

/** @type {Record<import("@/features/captain/day.js").BagStatus, [PillStatus, string]>} */
const PILL = {
  planned: ["planned", "toDeliver"],
  delivered: ["delivered", "delivered"],
  in_lobby: ["ready", "inLobby"],
  returned: ["issue", "returned"],
  reported: ["issue", "reported"],
  missing: ["issue", "missing"],
};

/**
 * The flats of one wing in walking order, the next door highlighted, and the scan at the door
 * that marks a bag delivered (design CaptainFlats; M7 US-4). Works offline.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/flats/")({ component: Flats });

function Flats() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const senior = useSession((s) => !!s.user?.senior_mode);
  const day = useCaptainDay();
  const write = useCaptainWrite();
  const [wing, setWing] = useState(/** @type {string | null} */ (null));
  const [last, setLast] = useState(
    /** @type {{ state: "success" | "error", text: string } | null} */ (null),
  );

  if (day.isPending) return <Skeleton lines={10} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const next = nextBag(d.bags);
  const wings = [...new Set(d.bags.map((b) => b.wing))];
  const shown = wing ?? next?.wing ?? wings[0];
  const bags = d.bags.filter((b) => b.wing === shown);

  const deliver = (/** @type {string} */ raw) => {
    const code = normalizeCode(raw);
    const bag = d.bags.find((b) => b.code === code);
    if (!bag || !isOpen(bag)) {
      setLast({
        state: "error",
        text: bag
          ? t("captain.flats.alreadyDone", { code, flat: bag.flat })
          : t("captain.flats.notToday", { code }),
      });
      return;
    }
    write(proof(bag, false));
    setWing(bag.wing);
    setLast({ state: "success", text: t("captain.flats.delivered", { code, flat: bag.flat }) });
  };

  return (
    <div className="flex flex-1 flex-col gap-2.5">
      <FieldHeader
        eyebrow={t("captain.flats.when", {
          society: d.society.name,
          day: dayLabel(d.date, i18n.resolvedLanguage ?? "en-IN"),
        })}
        title={t("captain.flats.title")}
      />
      <div>
        <SegmentedControl
          label={t("captain.flats.wing")}
          value={shown}
          onChange={setWing}
          options={wings.map((w) => ({ value: w, label: w }))}
        />
      </div>
      <ProgressBar
        tone="accent"
        label={t("captain.flats.progress", { wing: shown })}
        value={bags.filter((b) => b.status !== "planned").length}
        max={bags.length}
        valueLabel={t("captain.ofTotal", {
          done: bags.filter((b) => b.status !== "planned").length,
          total: bags.length,
        })}
      />
      <FlatChecklist
        label={t("captain.flats.progress", { wing: shown })}
        items={bags.map((b) => {
          const [status, word] = b.code === next?.code ? ["out", "next"] : PILL[b.status];
          return {
            flat: b.flat,
            note:
              b.pickup === "lobby"
                ? t("captain.flats.lobbyPickup")
                : t(`captain.flats.contents.${b.contents}`),
            status: /** @type {PillStatus} */ (status),
            statusLabel: t(`captain.flats.status.${word}`),
          };
        })}
      />
      {/* Kept above the tab bar while the list scrolls, so the next scan is always one tap away. */}
      <div
        className={`sticky mt-auto flex flex-col bg-bg pt-2 ${senior ? "bottom-[calc(76px+env(safe-area-inset-bottom))]" : "bottom-[calc(64px+env(safe-area-inset-bottom))]"}`}
      >
        {d.stop.handed_over_at ? (
          <ScanField
            label={t("captain.flats.scan")}
            manualLabel={t("captain.flats.manual")}
            expected={next?.code}
            state={last?.state}
            successLabel={last?.text}
            errorLabel={last?.text}
            onCode={deliver}
            onManual={() => navigate({ to: "/captain/flats/manual" })}
          />
        ) : (
          <Button
            variant="primary"
            size="lg"
            block
            onPress={() => navigate({ to: "/captain/scan" })}
          >
            {t("captain.flats.firstHandover")}
          </Button>
        )}
      </div>
    </div>
  );
}
