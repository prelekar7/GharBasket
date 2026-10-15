import {
  Button,
  FlatChecklist,
  ProgressBar,
  SegmentedControl,
  Sheet,
  Skeleton,
  TextField,
  toast,
} from "@gharbasket/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { bagQr, findFlat, isHandedOver, nextFlat, towerOf } from "@/features/captain/day.js";
import { dayLabel, isOpen, proof } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainWrite, useDrafts } from "@/features/captain/queries.js";
import { clockTime } from "@/features/subscriptions/format.js";
import { useSession } from "@/lib/session.js";

/** @typedef {Parameters<typeof import("@gharbasket/ui").StatusPill>[0]["status"]} PillStatus */

/** @type {Record<import("@/features/captain/day.js").FlatStatus, [PillStatus, string]>} */
const PILL = {
  on_the_way: ["planned", "onTheWay"],
  with_you: ["planned", "toDeliver"],
  held: ["ready", "held"],
  delivered: ["delivered", "delivered"],
  undelivered: ["issue", "undelivered"],
};

/**
 * The flats of one tower in walking order, the next door highlighted, and the scan at the door
 * that marks a bag delivered (design CaptainFlats; M7 §3.4). Works offline.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/flats/")({ component: Flats });

function Flats() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const senior = useSession((s) => !!s.user?.senior_mode);
  const day = useCaptainDay();
  const write = useCaptainWrite();
  const [wing, setWing] = useState(/** @type {string | null} */ (null));
  const [last, setLast] = useState(
    /** @type {{ state: "success" | "error", text: string } | null} */ (null),
  );
  const [drafts, setDrafts] = useDrafts(day.data?.date);
  const [picked, setPicked] = useState(/** @type {string | null} */ (null));

  if (day.isPending) return <Skeleton lines={10} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const issues = drafts.issues;
  const handedOver = isHandedOver(d);
  const next = nextFlat(d.flats.filter((f) => !issues[f.order_id]));
  const wings = [...new Set(d.flats.map(towerOf))];
  const shown = wing ?? (next && towerOf(next)) ?? wings[0];
  const flats = d.flats.filter((f) => towerOf(f) === shown);
  const handled = flats.filter((f) => f.status !== "on_the_way" && f.status !== "with_you").length;

  const deliver = (/** @type {string} */ raw) => {
    const flat = findFlat(d.flats, raw);
    if (!flat || !isOpen(flat)) {
      setLast({
        state: "error",
        text: flat
          ? t("captain.flats.alreadyDone", { flat: flat.flat_label })
          : t("captain.flats.notToday"),
      });
      return;
    }
    write(proof(flat));
    // The family turned up after all: the door scan settles a problem not yet sent.
    if (issues[flat.order_id]) setDrafts((x) => ({ ...x, issues: omit(x.issues, flat.order_id) }));
    setWing(towerOf(flat));
    setLast({ state: "success", text: t("captain.flats.delivered", { flat: flat.flat_label }) });
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
        value={handled}
        max={flats.length}
        valueLabel={t("captain.ofTotal", { done: handled, total: flats.length })}
      />
      {Object.keys(issues).length > 0 && (
        <Link
          to="/captain/flats/exceptions"
          className="flex min-h-12 items-center font-semibold text-link underline underline-offset-[3px]"
        >
          {t("captain.exceptions.count", { count: Object.keys(issues).length })}
        </Link>
      )}
      <FlatChecklist
        label={t("captain.flats.progress", { wing: shown })}
        onSelect={(item) =>
          setPicked(flats.find((f) => f.flat_label === item.flat)?.order_id ?? null)
        }
        items={flats.map((f) => {
          const issue = issues[f.order_id];
          const [status, word] = issue
            ? ["issue", issue.kind === "not_home" ? "notHome" : "damaged"]
            : f.order_id === next?.order_id
              ? ["out", "next"]
              : PILL[f.status];
          return {
            flat: f.flat_label,
            note: [
              t(`captain.flats.contents.${f.kind}`),
              f.redelivery ? t("captain.flats.redelivery") : null,
              f.hold_until
                ? t("captain.flats.holdUntil", { time: clockTime(f.hold_until, lang) })
                : null,
            ]
              .filter(Boolean)
              .join(" · "),
            status: /** @type {PillStatus} */ (status),
            statusLabel: t(`captain.flats.status.${word}`),
          };
        })}
      />
      {/* Kept above the tab bar while the list scrolls, so the next scan is always one tap away. */}
      <div
        className={`sticky mt-auto flex flex-col bg-bg pt-2 ${senior ? "bottom-[calc(76px+env(safe-area-inset-bottom))]" : "bottom-[calc(64px+env(safe-area-inset-bottom))]"}`}
      >
        {handedOver ? (
          <ScanField
            label={t("captain.flats.scan")}
            manualLabel={t("captain.flats.manual")}
            expected={next ? bagQr(next.order_id) : undefined}
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
      <FlatSheet
        flat={d.flats.find((f) => f.order_id === picked)}
        society={d.society.name}
        onClose={() => setPicked(null)}
        onIssue={(orderId, issue) => {
          setDrafts((x) => ({ ...x, issues: { ...x.issues, [orderId]: issue } }));
          setPicked(null);
          toast(t("captain.flat.added"));
        }}
      />
    </div>
  );
}

const omit = (/** @type {Record<string, any>} */ o, /** @type {string} */ k) =>
  Object.fromEntries(Object.entries(o).filter(([key]) => key !== k));

/**
 * A flat's bag, and the two problems a captain meets at a door: no one home, or something broken.
 * They wait on Exceptions until she saves them.
 * @param {{ flat: import("@/features/captain/day.js").Flat | undefined, society: string,
 *   onClose: () => void, onIssue: (orderId: string, issue: import("@/features/captain/queries.js").Issue) => void }} props
 */
function FlatSheet({ flat, society, onClose, onIssue }) {
  const { t } = useTranslation();
  const [note, setNote] = useState("");
  const [damaged, setDamaged] = useState(false);
  const open = flat?.status === "with_you";
  const close = () => {
    setNote("");
    setDamaged(false);
    onClose();
  };
  return (
    <Sheet
      open={!!flat}
      onOpenChange={(o) => !o && close()}
      title={flat?.flat_label ?? ""}
      footer={
        open &&
        flat &&
        (damaged ? (
          <Button
            variant="primary"
            size="lg"
            block
            isDisabled={!note.trim()}
            onPress={() => {
              onIssue(flat.order_id, { kind: "damaged", note: note.trim(), photo_key: null });
              close();
            }}
          >
            {t("captain.flat.report")}
          </Button>
        ) : (
          <>
            <Button size="lg" onPress={() => onIssue(flat.order_id, { kind: "not_home" })}>
              {t("captain.flat.notHome")}
            </Button>
            <Button size="lg" onPress={() => setDamaged(true)}>
              {t("captain.flat.damaged")}
            </Button>
          </>
        ))
      }
    >
      {flat && (
        <p className="m-0 text-base">
          {t("captain.flat.summary", {
            kind: t(`captain.flats.contents.${flat.kind}`),
            society,
            bags: flat.bags,
          })}
        </p>
      )}
      {open && damaged && (
        <TextField
          label={t("captain.flat.what")}
          placeholder={t("captain.flat.whatPh")}
          value={note}
          onChange={setNote}
          autoFocus
        />
      )}
    </Sheet>
  );
}
