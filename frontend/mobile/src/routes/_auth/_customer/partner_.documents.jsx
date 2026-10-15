import {
  Badge,
  Button,
  IconButton,
  OrderTimeline,
  Select,
  Sheet,
  Skeleton,
  TextField,
  toast,
} from "@gharbasket/ui";
import { ArrowLeftIcon, PlusIcon } from "@phosphor-icons/react";
import { createFileRoute, Navigate, useNavigate, useRouter } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import {
  useAddKycDocument,
  useApplicationDocuments,
  useMyApplication,
} from "@/features/partners/queries.js";
import { ApiError } from "@/lib/api.js";

/** @typedef {import("@/features/partners/queries.js").DocRequirement} DocRequirement */
/** @typedef {import("@/features/partners/queries.js").KycDocument} KycDocument */
/** Never Aadhaar: the ID kinds the server takes (M8 §3.1). */
const ID_KINDS = ["voter_id", "passport", "driving_licence"];
/** How the server checks each number (M8 §3.1); a 12-digit number is Aadhaar and is refused. */
const FORMAT = /** @type {Record<string, RegExp>} */ ({
  voter_id: /^[A-Z]{3}\d{7}$/,
  passport: /^[A-Z]\d{7}$/,
  pan: /^[A-Z]{5}\d{4}[A-Z]$/,
  driving_licence: /^[A-Z0-9]{15,16}$/,
  fssai: /^\d{14}$/,
  bank: /^\d{9,18}$/,
  vehicle: /^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$/,
});
/** Files each document may carry: kitchen photos 2 to 4, anything else up to 2. */
const FILES = /** @type {Record<string, [number, number]>} */ ({ kitchen_photos: [2, 4] });

/** Become a partner, step 2: your documents, added or to do, and how they are kept (design HcPartnerDocs, M8 US-2). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/partner_/documents")({
  component: PartnerDocuments,
});

function PartnerDocuments() {
  const { t } = useTranslation();
  const router = useRouter();
  const navigate = useNavigate();
  const application = useMyApplication();
  const [adding, setAdding] = useState(/** @type {DocRequirement | null} */ (null));

  const app = application.data;
  const docs = useApplicationDocuments(app?.id);
  const header = (
    <header className="-ml-3 flex items-center gap-1">
      <IconButton
        label={t("onboarding.back")}
        variant="quiet"
        onPress={() => router.history.back()}
      >
        <ArrowLeftIcon size={24} />
      </IconButton>
      <div className="flex flex-col">
        {app && (
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("partnerDocs.step", { kind: app.kind })}
          </span>
        )}
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("partnerDocs.title")}
        </h1>
      </div>
    </header>
  );
  if (application.isPending || (app && docs.isPending))
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  // No application yet: documents come after applying.
  if (application.data === null) return <Navigate to="/partner" />;
  if (application.isError || docs.isError || !app || !docs.data)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError
          onRetry={() => {
            application.refetch();
            docs.refetch();
          }}
        />
      </div>
    );

  const items = app.requirements.documents;
  const sent = (/** @type {DocRequirement} */ d) =>
    d.status !== "missing" && d.status !== "rejected";
  const added = items.filter(sent).length;
  const next = items.find((d) => !sent(d));
  const detail = (/** @type {DocRequirement} */ d) => docs.data.find((x) => x.id === d.document_id);
  /** The line under a document's name: what to send, why it came back, or what was sent. @param {DocRequirement} d */
  const note = (d) => {
    const doc = detail(d);
    if (d.status === "rejected" && d.reason) return d.reason;
    if (!doc) return t(`partnerDocs.docs.${d.doc_type}.note`);
    if (doc.check.status === "failed") return t("partnerDocs.checkFailed");
    return t(`partnerDocs.docs.${d.doc_type}.added`, {
      idKind: doc.id_kind ? t(`partnerDocs.idKinds.${doc.id_kind}`) : "",
      masked: doc.number_masked ?? "",
      n: doc.file_count,
      defaultValue: t(`partnerDocs.docs.${d.doc_type}.note`),
    });
  };

  return (
    <div className="flex min-h-[calc(100dvh-160px)] flex-col gap-3">
      {header}
      <OrderTimeline
        label={t("partnerDocs.progress")}
        steps={[
          { label: t("partnerDocs.steps.application"), state: "done" },
          {
            label: t("partnerDocs.steps.documents"),
            state: next ? "current" : "done",
            note: next ? t("partnerDocs.addedOf", { added, total: items.length }) : undefined,
          },
          { label: t("partnerDocs.steps.training"), state: next ? "todo" : "current" },
          { label: t("partnerDocs.steps.hub"), state: "todo" },
        ]}
        labels={{
          done: t("orders.tl.done"),
          current: t("orders.tl.current"),
          issue: t("orders.tl.issue"),
          todo: t("orders.tl.todo"),
        }}
      />
      <ul>
        {items.map((d) => (
          <li
            key={d.doc_type}
            className="flex min-h-16 items-center justify-between gap-3 border-b border-line py-2.5"
          >
            <span className="flex flex-col">
              <strong>{t(`partnerDocs.docs.${d.doc_type}.name`)}</strong>
              <span className="text-sm leading-5 text-fg-secondary">{note(d)}</span>
            </span>
            <Badge tone={sent(d) ? "primary" : "outline"}>
              {t(`partnerDocs.status.${d.status}`)}
            </Badge>
          </li>
        ))}
      </ul>
      <p className="text-sm leading-5 text-fg-secondary">{t("partnerDocs.safe")}</p>
      <div className="mt-auto">
        {next ? (
          <Button variant="primary" size="lg" block onPress={() => setAdding(next)}>
            <PlusIcon aria-hidden size={20} weight="bold" />
            {t("partnerDocs.cta")}
          </Button>
        ) : (
          <Button variant="primary" size="lg" block onPress={() => navigate({ to: "/training" })}>
            {t("partnerDocs.done")}
          </Button>
        )}
      </div>
      {adding && (
        <AddDocument applicationId={app.id} doc={adding} onClose={() => setAdding(null)} />
      )}
    </div>
  );
}

/**
 * One document's sheet: the number (only its last 4 characters are ever shown again), the details
 * that kind of document carries, and the photo or PDF.
 * @param {{ applicationId: string, doc: DocRequirement, onClose: () => void }} props
 */
function AddDocument({ applicationId, doc, onClose }) {
  const { t } = useTranslation();
  const add = useAddKycDocument();
  const [idKind, setIdKind] = useState("voter_id");
  const [number, setNumber] = useState("");
  const [ifsc, setIfsc] = useState("");
  const [holder, setHolder] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [vehicleNo, setVehicleNo] = useState("");
  const [files, setFiles] = useState(/** @type {File[]} */ ([]));
  const input = useRef(/** @type {HTMLInputElement | null} */ (null));
  const type = doc.doc_type;
  const name = t(`partnerDocs.docs.${type}.name`);
  const [minFiles, maxFiles] = FILES[type] ?? [0, 2];
  const hasNumber = type !== "kitchen_photos";
  const numberOk = !hasNumber || FORMAT[type === "id_proof" ? idKind : type]?.test(number);
  const ifscOk = type !== "bank" || /^[A-Z]{4}0[A-Z0-9]{6}$/.test(ifsc);
  const holderOk = type !== "bank" || holder.trim().length > 0;
  const hasExpiry = type === "fssai" || type === "driving_licence";
  const expiryOk = !hasExpiry || validUntil > new Date().toISOString().slice(0, 10);
  const vehicleOk = type !== "driving_licence" || FORMAT.vehicle.test(vehicleNo);
  const ready = numberOk && ifscOk && holderOk && expiryOk && vehicleOk && files.length >= minFiles;
  /** What the server said, in words; Aadhaar and expiry have their own. @param {unknown} e */
  const why = (e) =>
    t(
      e instanceof ApiError && e.code.startsWith("partners.")
        ? `partnerDocs.sheet.errors.${e.code.slice(9)}`
        : "partnerDocs.sheet.failed",
      { defaultValue: t("partnerDocs.sheet.failed") },
    );

  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={t("partnerDocs.sheet.title", { doc: name })}
      description={t(type === "bank" ? "partnerDocs.sheet.leadBank" : "partnerDocs.sheet.lead")}
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={!ready}
          isPending={add.isPending}
          onPress={() =>
            add.mutate(
              {
                applicationId,
                docType: type,
                files,
                idKind: type === "id_proof" ? idKind : undefined,
                number: hasNumber ? number : undefined,
                ifsc: type === "bank" ? ifsc : undefined,
                accountHolder: type === "bank" ? holder.trim() : undefined,
                validUntil: hasExpiry ? validUntil : undefined,
                vehicleNo: type === "driving_licence" ? vehicleNo : undefined,
              },
              {
                onSuccess: () => {
                  toast(t("partnerDocs.sheet.saved", { doc: name }));
                  onClose();
                },
                onError: (e) => toast(why(e)),
              },
            )
          }
        >
          {t("partnerDocs.sheet.add")}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        {type === "id_proof" && (
          <Select
            label={t("partnerDocs.sheet.idKind")}
            value={idKind}
            onChange={setIdKind}
            options={ID_KINDS.map((k) => ({ value: k, label: t(`partnerDocs.idKinds.${k}`) }))}
          />
        )}
        {hasNumber && (
          <TextField
            label={t(`partnerDocs.sheet.number.${type}`)}
            description={t("partnerDocs.sheet.masked")}
            inputMode={type === "fssai" || type === "bank" ? "numeric" : "text"}
            autoComplete="off"
            value={number}
            onChange={(v) =>
              setNumber(
                v
                  .toUpperCase()
                  .replace(/[^A-Z0-9]/g, "")
                  .slice(0, 20),
              )
            }
            isInvalid={number.length > 0 && !numberOk}
            errorMessage={t(`partnerDocs.sheet.invalid.${type}`)}
          />
        )}
        {type === "bank" && (
          <>
            <TextField
              label={t("partnerDocs.sheet.ifsc")}
              autoComplete="off"
              value={ifsc}
              onChange={(v) =>
                setIfsc(
                  v
                    .toUpperCase()
                    .replace(/[^A-Z0-9]/g, "")
                    .slice(0, 11),
                )
              }
              isInvalid={ifsc.length > 0 && !ifscOk}
              errorMessage={t("partnerDocs.sheet.invalid.ifsc")}
            />
            <TextField
              label={t("partnerDocs.sheet.holder")}
              autoComplete="off"
              value={holder}
              onChange={setHolder}
            />
          </>
        )}
        {type === "driving_licence" && (
          <TextField
            label={t("partnerDocs.sheet.vehicle")}
            autoComplete="off"
            value={vehicleNo}
            onChange={(v) =>
              setVehicleNo(
                v
                  .toUpperCase()
                  .replace(/[^A-Z0-9]/g, "")
                  .slice(0, 12),
              )
            }
            isInvalid={vehicleNo.length > 0 && !vehicleOk}
            errorMessage={t("partnerDocs.sheet.invalid.vehicle")}
          />
        )}
        {hasExpiry && (
          <TextField
            label={t("partnerDocs.sheet.validUntil")}
            type="date"
            value={validUntil}
            onChange={setValidUntil}
            isInvalid={validUntil.length > 0 && !expiryOk}
            errorMessage={t("partnerDocs.sheet.errors.document_expired")}
          />
        )}
        {type !== "bank" && (
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={input}
              type="file"
              accept={type === "kitchen_photos" ? "image/*" : "image/*,application/pdf"}
              multiple
              hidden
              onChange={(e) => setFiles([...(e.target.files ?? [])].slice(0, maxFiles))}
            />
            <Button variant="secondary" onPress={() => input.current?.click()}>
              <PlusIcon aria-hidden size={20} weight="bold" />
              {t(type === "kitchen_photos" ? "partnerDocs.sheet.photos" : "partnerDocs.sheet.file")}
            </Button>
            {files.length > 0 && (
              <span className="text-sm text-fg-secondary">
                {t("partnerDocs.sheet.chosen", { n: files.length })}
              </span>
            )}
          </div>
        )}
      </div>
    </Sheet>
  );
}
