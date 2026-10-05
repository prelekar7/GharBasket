import {
  BagLabel,
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
import { nextBag } from "@/features/captain/day.js";
import { dayLabel, isOpen, proof } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainWrite, useDrafts } from "@/features/captain/queries.js";
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
  const [drafts, setDrafts] = useDrafts(day.data?.date);
  const [picked, setPicked] = useState(/** @type {string | null} */ (null));

  if (day.isPending) return <Skeleton lines={10} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const issues = drafts.issues;
  const next = nextBag(d.bags.filter((b) => !issues[b.code]));
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
    // The family turned up after all: the door scan settles a problem not yet sent.
    if (issues[code]) setDrafts((x) => ({ ...x, issues: omit(x.issues, code) }));
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
        onSelect={(item) => setPicked(bags.find((b) => b.flat === item.flat)?.code ?? null)}
        items={bags.map((b) => {
          const issue = issues[b.code];
          const [status, word] = issue
            ? ["issue", issue.kind === "not_home" ? "notHome" : "damaged"]
            : b.code === next?.code
              ? ["out", "next"]
              : PILL[b.status];
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
      <FlatSheet
        bag={d.bags.find((b) => b.code === picked)}
        society={d.society.name}
        canReport={!!d.stop.handed_over_at}
        onClose={() => setPicked(null)}
        onIssue={(code, issue) => {
          setDrafts((x) => ({ ...x, issues: { ...x.issues, [code]: issue } }));
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
 * A flat's bag label, and the two problems a captain meets at a door: no one home, or something
 * broken. They wait on Exceptions until she saves them.
 * @param {{ bag: import("@/features/captain/day.js").Bag | undefined, society: string, canReport: boolean,
 *   onClose: () => void, onIssue: (code: string, issue: import("@/features/captain/queries.js").Issue) => void }} props
 */
function FlatSheet({ bag, society, canReport, onClose, onIssue }) {
  const { t } = useTranslation();
  const [note, setNote] = useState("");
  const [damaged, setDamaged] = useState(false);
  const open = !!bag && canReport && bag.status === "planned";
  const close = () => {
    setNote("");
    setDamaged(false);
    onClose();
  };
  return (
    <Sheet
      open={!!bag}
      onOpenChange={(o) => !o && close()}
      title={bag?.flat ?? ""}
      footer={
        open &&
        bag &&
        (damaged ? (
          <Button
            variant="primary"
            size="lg"
            block
            isDisabled={!note.trim()}
            onPress={() => {
              onIssue(bag.code, { kind: "damaged", note: note.trim(), photo_key: null });
              close();
            }}
          >
            {t("captain.flat.report")}
          </Button>
        ) : (
          <>
            <Button
              size="lg"
              onPress={() => onIssue(bag.code, { kind: "not_home", outcome: "lobby" })}
            >
              {t("captain.flat.notHome")}
            </Button>
            <Button size="lg" onPress={() => setDamaged(true)}>
              {t("captain.flat.damaged")}
            </Button>
          </>
        ))
      }
    >
      {bag && (
        <BagLabel
          code={bag.code}
          flat={bag.flat}
          society={society}
          crate={bag.crate}
          lines={bag.lines}
          flags={bag.contents === "box" ? [] : ["tiffin"]}
          labels={{
            bag: t("captain.flat.bag"),
            flat: t("captain.flat.flatWord"),
            crate: t("captain.flat.crate", { crate: "{crate}" }),
            lines: t("captain.flat.lines", { n: "{n}" }),
            flags: { tiffin: t("captain.flats.contents.tiffin") },
          }}
        />
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
