import { Button, TextField } from "@gharbasket/ui";
import { CameraIcon, CheckIcon } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { clockTime, istClock, shortDate, weekdayName } from "@/features/subscriptions/format.js";
import { ApiError } from "@/lib/api.js";
import { uploadFile } from "@/lib/upload.js";
import { bagQr } from "./day.js";
import { useTiffinProof } from "./queries.js";

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
 * At a tiffin's door (M7 §3.7): its label, then the family's 4-digit code or a photo. A tiffin has
 * no bag to scan, and she can only settle one that was handed to her. Sent at once, so a wrong
 * code is heard at the door; after five wrong ones only the photo works.
 * @param {{ flat: import("./day.js").Flat, onDone: () => void }} props
 */
export function TiffinDoor({ flat, onDone }) {
  const { t } = useTranslation();
  const prove = useTiffinProof();
  const [code, setCode] = useState("");
  const [error, setError] = useState(/** @type {string | null} */ (null));
  const [busy, setBusy] = useState(false);
  const send = async (/** @type {Record<string, unknown>} */ how) => {
    setBusy(true);
    setError(null);
    try {
      await prove(flat.order_id, {
        short_code: flat.short_code,
        ...how,
        client_at: new Date().toISOString(),
      });
      onDone();
    } catch (e) {
      const problem = e instanceof ApiError ? e.code : "";
      setError(
        {
          "logistics.code_invalid": t("captain.tiffin.codeWrong"),
          "logistics.code_attempts_exceeded": t("captain.tiffin.codeLocked"),
          "logistics.delivery_not_found": t("captain.tiffin.notWithYou"),
          "logistics.bag_mismatch": t("captain.tiffin.wrongLabel"),
          "logistics.delivery_closed": t("captain.tiffin.closed"),
        }[problem] ?? t("captain.tiffin.failed"),
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-2">
      <TextField
        label={t("captain.tiffin.codeLabel")}
        description={t("captain.tiffin.codeHelp")}
        inputMode="numeric"
        maxLength={4}
        autoComplete="off"
        value={code}
        onChange={setCode}
        isInvalid={!!error}
        errorMessage={error ?? undefined}
      />
      <Button
        variant="primary"
        size="lg"
        block
        isPending={busy}
        isDisabled={!/^\d{4}$/.test(code.trim())}
        onPress={() => send({ proof: "otp", delivery_code: code.trim() })}
      >
        {t("captain.tiffin.deliver", { flat: flat.flat_label })}
      </Button>
      <PhotoButton
        added={false}
        label={t("captain.tiffin.photo")}
        doneLabel={t("captain.exceptions.photoAdded")}
        onKey={(key) => send({ proof: "photo", photo_key: key })}
      />
    </div>
  );
}

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
