import { Button, Sheet, Skeleton, StatusPill, TextField } from "@gharbasket/ui";
import { CameraIcon } from "@phosphor-icons/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { uploadFile } from "@/lib/upload.js";
import { useDoorActions, useDoors } from "./queries.js";

const FAILS = /** @type {const} */ (["not_home", "refused", "wrong_address", "damaged", "missing"]);

/**
 * The last metres at a rider-mode stop: each bag with "Delivered" (the customer's code or a photo
 * of the bag at the door) and "Couldn't deliver" (a reason). M7 §3.4; a captain does this herself.
 * @param {{ stopId: string }} props
 */
export function Doors({ stopId }) {
  const { t } = useTranslation();
  const doors = useDoors(stopId, true);
  const actions = useDoorActions(stopId);
  const [asking, setAsking] = useState(
    /** @type {{ door: import("./queries.js").Door, mode: "deliver" | "fail" } | null} */ (null),
  );
  if (doors.isPending) return <Skeleton lines={4} />;
  const items = doors.data ?? [];
  return (
    <section className="flex flex-col gap-2">
      <h2 className="m-0 text-[19px] leading-[26px] font-bold">{t("rider.stop.doors")}</h2>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {items.map((d) => (
          <li
            key={d.order_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-surface px-3.5 py-2.5"
          >
            <strong className="tabular-nums">{d.flat_label}</strong>
            {d.status === "delivered" || d.status === "undelivered" ? (
              <StatusPill status={d.status === "delivered" ? "delivered" : "issue"}>
                {t(d.status === "delivered" ? "rider.stop.delivered" : "rider.stop.notDelivered")}
              </StatusPill>
            ) : (
              <span className="flex gap-2">
                <Button onPress={() => setAsking({ door: d, mode: "fail" })}>
                  {t("rider.stop.cantDeliver")}
                </Button>
                <Button variant="primary" onPress={() => setAsking({ door: d, mode: "deliver" })}>
                  {t("rider.stop.deliver")}
                </Button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {asking?.mode === "deliver" && (
        <ProofSheet
          key={asking.door.order_id}
          door={asking.door}
          onClose={() => setAsking(null)}
          onProof={(proof) => actions.deliver(asking.door, proof)}
        />
      )}
      <Sheet
        open={asking?.mode === "fail"}
        onOpenChange={(open) => !open && setAsking(null)}
        title={t("rider.stop.failTitle")}
      >
        <div className="flex flex-col gap-2">
          {FAILS.map((reason) => (
            <Button
              key={reason}
              block
              onPress={() => {
                if (asking) actions.undeliver(asking.door, reason);
                setAsking(null);
              }}
            >
              {t(`rider.stop.failReasons.${reason}`)}
            </Button>
          ))}
        </div>
      </Sheet>
    </section>
  );
}

/** @param {{ door: import("./queries.js").Door, onClose: () => void, onProof: (proof: { proof: "otp", delivery_code: string } | { proof: "photo", photo_key: string }) => unknown }} props */
function ProofSheet({ door, onClose, onProof }) {
  const { t } = useTranslation();
  const camera = useRef(/** @type {HTMLInputElement | null} */ (null));
  const [code, setCode] = useState("");
  const [photoKey, setPhotoKey] = useState(/** @type {string | null} */ (null));
  const [failed, setFailed] = useState(false);
  const ready = /^\d{4}$/.test(code) || !!photoKey;
  return (
    <Sheet
      open
      onOpenChange={(open) => !open && onClose()}
      title={t("rider.stop.proofTitle", { flat: door.flat_label })}
      footer={
        <Button
          variant="primary"
          size="lg"
          isDisabled={!ready}
          onPress={() => {
            onProof(
              /^\d{4}$/.test(code)
                ? { proof: "otp", delivery_code: code }
                : { proof: "photo", photo_key: photoKey ?? "" },
            );
            onClose();
          }}
        >
          {t("rider.stop.deliver")}
        </Button>
      }
    >
      <div className="flex flex-col gap-2.5">
        <TextField
          label={t("rider.stop.doorCode")}
          description={t("rider.stop.doorCodeHelp")}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={4}
          placeholder="• • • •"
          value={code}
          onChange={(v) => setCode(v.replace(/\D/g, "").slice(0, 4))}
        />
        <input
          ref={camera}
          type="file"
          accept="image/*"
          capture="environment"
          hidden
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            // The photo goes to storage first; only its key waits in the outbox.
            // "delivery_photo" is M7's purpose for M0's presign (not in M0's list yet).
            const key = await uploadFile(file, /** @type {any} */ ("delivery_photo")).catch(
              () => null,
            );
            setPhotoKey(key);
            setFailed(!key);
          }}
        />
        {photoKey && <span className="font-semibold">{t("rider.stop.photoAdded")}</span>}
        {failed && (
          <p role="alert" className="text-sm font-semibold text-danger">
            {t("rider.stop.photoFailed")}
          </p>
        )}
        <Button block onPress={() => camera.current?.click()}>
          <CameraIcon aria-hidden size={20} />
          {photoKey ? t("rider.stop.photoAgain") : t("rider.stop.photo")}
        </Button>
      </div>
    </Sheet>
  );
}
