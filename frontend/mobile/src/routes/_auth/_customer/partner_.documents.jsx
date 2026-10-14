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
  useKycDocuments,
  useMyApplication,
} from "@/features/partners/queries.js";

/** @typedef {import("@/features/partners/queries.js").KycDocument} KycDocument */
const ID_KINDS = ["pan", "voter_id", "driving_licence", "passport"];
const FILES_NEEDED = { photo_id: 1, fssai: 1, bank: 0, kitchen_photos: 3 };

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
  const docs = useKycDocuments();
  const [adding, setAdding] = useState(/** @type {KycDocument | null} */ (null));

  const app = application.data;
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
  if (application.isError || docs.isError || !docs.data)
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

  const items = docs.data;
  const added = items.filter((d) => d.status === "added").length;
  const next = items.find((d) => d.status !== "added");
  /** The line under a document's name; once added, its masked number or file count. @param {KycDocument} d */
  const note = (d) =>
    d.status !== "added"
      ? t(`partnerDocs.docs.${d.doc_type}.note`)
      : t(`partnerDocs.docs.${d.doc_type}.added`, {
          idKind: d.id_kind ? t(`partnerDocs.idKinds.${d.id_kind}`) : "",
          masked: d.masked ?? "",
          n: d.files,
          defaultValue: t(`partnerDocs.docs.${d.doc_type}.note`),
        });

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
            <Badge tone={d.status === "added" ? "primary" : "outline"}>
              {t(d.status === "added" ? "partnerDocs.added" : "partnerDocs.todo")}
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
      {adding && <AddDocument doc={adding} onClose={() => setAdding(null)} />}
    </div>
  );
}

/**
 * One document's sheet: the number (only its last 4 are ever shown) and the photo or PDF.
 * @param {{ doc: KycDocument, onClose: () => void }} props
 */
function AddDocument({ doc, onClose }) {
  const { t } = useTranslation();
  const add = useAddKycDocument();
  const [idKind, setIdKind] = useState("pan");
  const [number, setNumber] = useState("");
  const [ifsc, setIfsc] = useState("");
  const [files, setFiles] = useState(/** @type {File[]} */ ([]));
  const input = useRef(/** @type {HTMLInputElement | null} */ (null));
  const type = doc.doc_type;
  const name = t(`partnerDocs.docs.${type}.name`);
  const need = FILES_NEEDED[type];
  const numberOk =
    type === "photo_id"
      ? /^[A-Z0-9]{4,20}$/.test(number)
      : type === "fssai"
        ? /^\d{14}$/.test(number)
        : type === "bank"
          ? /^\d{9,18}$/.test(number)
          : true;
  const ifscOk = type !== "bank" || /^[A-Z]{4}0[A-Z0-9]{6}$/.test(ifsc);
  const ready = numberOk && ifscOk && files.length >= need;

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
              { docType: type, files, idKind, number, ifsc },
              {
                onSuccess: () => {
                  toast(t("partnerDocs.sheet.saved", { doc: name }));
                  onClose();
                },
                onError: () => toast(t("partnerDocs.sheet.failed")),
              },
            )
          }
        >
          {t("partnerDocs.sheet.add")}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        {type === "photo_id" && (
          <Select
            label={t("partnerDocs.sheet.idKind")}
            value={idKind}
            onChange={setIdKind}
            options={ID_KINDS.map((k) => ({ value: k, label: t(`partnerDocs.idKinds.${k}`) }))}
          />
        )}
        {type !== "kitchen_photos" && (
          <TextField
            label={t(`partnerDocs.sheet.number.${type}`)}
            description={t("partnerDocs.sheet.masked")}
            inputMode={type === "photo_id" ? "text" : "numeric"}
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
        )}
        {need > 0 && (
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={input}
              type="file"
              accept={type === "kitchen_photos" ? "image/*" : "image/*,application/pdf"}
              multiple={type === "kitchen_photos"}
              hidden
              onChange={(e) => setFiles([...(e.target.files ?? [])].slice(0, 6))}
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
