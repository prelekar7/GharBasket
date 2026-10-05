import { Button, EmptyState, RadioCards, Skeleton, toast } from "@gharbasket/ui";
import { CameraIcon, CheckIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { issueAction } from "@/features/captain/parts.jsx";
import {
  uploadPhoto,
  useCaptainDay,
  useCaptainWrite,
  useDrafts,
} from "@/features/captain/queries.js";

/**
 * Problems from the doors, sorted out in one go: where a "no one home" bag goes, and a photo of
 * anything broken. Saving sends them and the families are told (design CaptainExceptions; M7 US-6).
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/flats/exceptions")({ component: Exceptions });

function Exceptions() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const day = useCaptainDay();
  const write = useCaptainWrite();
  const [drafts, setDrafts] = useDrafts(day.data?.date);
  const toFlats = () => navigate({ to: "/captain/flats" });

  if (day.isPending) return <Skeleton lines={8} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  // Bags waiting at a door first, then breakages, each in walking order.
  const open = ["not_home", "damaged"].flatMap((kind) =>
    d.bags.filter((bag) => drafts.issues[bag.code]?.kind === kind),
  );
  /** @param {string} code @param {Partial<import("@/features/captain/queries.js").Issue>} patch */
  const change = (code, patch) =>
    setDrafts((x) => ({ ...x, issues: { ...x.issues, [code]: { ...x.issues[code], ...patch } } }));

  return (
    <div className="flex flex-1 flex-col gap-3">
      <FieldHeader
        back={{ label: t("captain.exceptions.back"), onPress: toFlats }}
        eyebrow={t("captain.exceptions.count", { count: open.length })}
        title={t("captain.exceptions.title")}
      />
      {open.length === 0 && (
        <EmptyState title={t("captain.exceptions.none")} body={t("captain.exceptions.noneBody")} />
      )}
      {open.map((bag) => {
        const issue = drafts.issues[bag.code];
        return issue.kind === "not_home" ? (
          <section
            key={bag.code}
            className="flex flex-col gap-2.5 rounded-md border border-line-strong p-3.5"
          >
            <h2 className="m-0 text-[19px] leading-[26px] font-bold">
              {t("captain.exceptions.notHome", { flat: bag.flat })}
            </h2>
            <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
              {t("captain.exceptions.notHomeBody")}
            </p>
            <RadioCards
              label={t("captain.exceptions.notHome", { flat: bag.flat })}
              value={issue.outcome ?? "lobby"}
              onChange={(v) => change(bag.code, { outcome: v === "return" ? "return" : "lobby" })}
              options={["lobby", "return"].map((v) => ({
                value: v,
                title: t(`captain.exceptions.${v}`),
                description: t(`captain.exceptions.${v}Desc`),
              }))}
            />
          </section>
        ) : (
          <section key={bag.code} className="flex flex-col gap-2 rounded-md bg-danger-soft p-3.5">
            <h2 className="m-0 text-[19px] leading-[26px] font-bold">
              {t("captain.exceptions.damaged", { flat: bag.flat, note: issue.note })}
            </h2>
            <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
              {t("captain.exceptions.damagedBody")}
            </p>
            <DamagePhoto
              added={!!issue.photo_key}
              onKey={(key) => change(bag.code, { photo_key: key })}
            />
          </section>
        );
      })}
      {open.length > 0 && (
        <div className="mt-auto">
          <Button
            variant="primary"
            size="lg"
            block
            onPress={() => {
              for (const bag of open) write(issueAction(bag, drafts.issues[bag.code]));
              setDrafts((x) => ({ ...x, issues: {} }));
              toast(t("captain.exceptions.saved"));
              toFlats();
            }}
          >
            {t("captain.exceptions.save")}
          </Button>
        </div>
      )}
    </div>
  );
}

/** Takes the photo with the phone's camera and uploads it at once; only the key is kept. @param {{ added: boolean, onKey: (key: string) => void }} props */
function DamagePhoto({ added, onKey }) {
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
          await uploadPhoto(file)
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
        {added ? t("captain.exceptions.photoAdded") : t("captain.exceptions.photo")}
      </Button>
      {failed && (
        <p role="alert" className="m-0 text-sm font-semibold text-danger">
          {t("captain.exceptions.photoFailed")}
        </p>
      )}
    </>
  );
}
