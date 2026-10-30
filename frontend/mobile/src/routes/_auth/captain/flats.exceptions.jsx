import { Button, EmptyState, Skeleton, toast } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { issueAction, PhotoButton } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainWrite, useDrafts } from "@/features/captain/queries.js";

/**
 * Problems from the doors, sorted out in one go: a "no one home" bag is held for the family, and a
 * photo of anything broken. Saving sends them and the families are told (design CaptainExceptions;
 * M7 §3.4).
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
    d.flats.filter((f) => drafts.issues[f.order_id]?.kind === kind),
  );
  /** @param {string} orderId @param {Partial<import("@/features/captain/queries.js").Issue>} patch */
  const change = (orderId, patch) =>
    setDrafts((x) => ({
      ...x,
      issues: { ...x.issues, [orderId]: { ...x.issues[orderId], ...patch } },
    }));

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
      {open.map((flat) => {
        const issue = drafts.issues[flat.order_id];
        return issue.kind === "not_home" ? (
          <section
            key={flat.order_id}
            className="flex flex-col gap-2.5 rounded-md border border-line-strong p-3.5"
          >
            <h2 className="m-0 text-[19px] leading-[26px] font-bold">
              {t("captain.exceptions.notHome", { flat: flat.flat_label })}
            </h2>
            <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
              {t(
                flat.kind === "tiffin"
                  ? "captain.exceptions.tiffinBody"
                  : "captain.exceptions.notHomeBody",
              )}
            </p>
          </section>
        ) : (
          <section
            key={flat.order_id}
            className="flex flex-col gap-2 rounded-md bg-danger-soft p-3.5"
          >
            <h2 className="m-0 text-[19px] leading-[26px] font-bold">
              {t("captain.exceptions.damaged", { flat: flat.flat_label, note: issue.note })}
            </h2>
            <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
              {t("captain.exceptions.damagedBody")}
            </p>
            <PhotoButton
              added={!!issue.photo_key}
              label={t("captain.exceptions.photo")}
              doneLabel={t("captain.exceptions.photoAdded")}
              onKey={(key) => change(flat.order_id, { photo_key: key })}
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
              for (const flat of open) write(issueAction(flat, drafts.issues[flat.order_id]));
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
