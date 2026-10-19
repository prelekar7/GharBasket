import { Button } from "@gharbasket/ui";
import { CameraIcon, CheckIcon } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { clockTime, istClock, shortDate, weekdayName } from "@/features/subscriptions/format.js";
import { uploadFile } from "@/lib/upload.js";
import { bagQr } from "./day.js";

/** "Tue 6 Oct" / "मंगल 6 अक्टू॰". @param {string} iso @param {string} lang */
export const dayLabel = (iso, lang) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;

/** "8:05 am" / "सुबह 8:05" from an ISO time. @param {string} at @param {string} lang */
export const clock = (at, lang) => clockTime(istClock(at), lang);

/** After the handover: what to do next. */
export function ReceivedCard() {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-1.5 rounded-md bg-accent-soft p-3.5">
      <h2 className="m-0 text-[19px] leading-[26px] font-bold">{t("captain.received.title")}</h2>
      <p className="m-0">{t("captain.received.body")}</p>
    </section>
  );
}

/** A bag the captain can still hand over: with her, or held for a family that turned up. @param {import("./day.js").Flat} flat */
export const isOpen = (flat) => flat.status === "with_you" || flat.status === "held";

/** Delivered at the door, scanned by the captain: POST /v1/deliveries/{order_id}/proof. @param {import("./day.js").Flat} flat */
export const proof = (flat) => ({
  path: `/v1/deliveries/${flat.order_id}/proof`,
  body: { bag_qr: bagQr(flat.order_id), proof: "scan", client_at: new Date().toISOString() },
});

/**
 * Sends the problems sorted out on Exceptions: not home → POST …/undelivered (the bag is held for
 * the family until 8 PM), damaged → POST …/undelivered with the captain's words and the photo.
 * @param {import("./day.js").Flat} flat @param {import("./queries.js").Issue} issue
 * @returns {import("./day.js").Action}
 */
export const issueAction = (flat, issue) => ({
  path: `/v1/deliveries/${flat.order_id}/undelivered`,
  body:
    issue.kind === "not_home"
      ? { reason: "not_home", client_at: new Date().toISOString() }
      : {
          reason: "damaged",
          note: issue.note,
          ...(issue.photo_key ? { photo_key: issue.photo_key } : {}),
          client_at: new Date().toISOString(),
        },
});

/**
 * Takes the photo with the phone's camera and uploads it at once (purpose `delivery_photo`, M7
 * §3.4); only the key is kept.
 * @param {{ added: boolean, label: string, doneLabel: string, onKey: (key: string) => void }} props
 */
export function PhotoButton({ added, label, doneLabel, onKey }) {
  const { t } = useTranslation();
  const input = useRef(/** @type {HTMLInputElement | null} */ (null));
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          setBusy(true);
          setFailed(false);
          await uploadFile(file, "delivery_photo")
            .then(onKey, () => setFailed(true))
            .finally(() => setBusy(false));
        }}
      />
      <Button block isPending={busy} onPress={() => input.current?.click()}>
        {added ? (
          <CheckIcon aria-hidden size={20} weight="bold" />
        ) : (
          <CameraIcon aria-hidden size={20} />
        )}
        {added ? doneLabel : label}
      </Button>
      {failed && (
        <p role="alert" className="m-0 text-sm font-semibold text-danger">
          {t("captain.exceptions.photoFailed")}
        </p>
      )}
    </>
  );
}
