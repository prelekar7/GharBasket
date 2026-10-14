import { Button, Skeleton, TextField, toast } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { findFlat, isHandedOver } from "@/features/captain/day.js";
import { isOpen, proof } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainWrite } from "@/features/captain/queries.js";

/** A torn or smudged label: type the flat, check it against the label, hand it over (design CaptainManual; M7 §3.4). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/flats/manual")({ component: TypeBagCode });

function TypeBagCode() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const day = useCaptainDay();
  const write = useCaptainWrite();
  const [raw, setRaw] = useState("");
  const toFlats = () => navigate({ to: "/captain/flats" });

  if (day.isPending) return <Skeleton lines={6} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const flat = raw.trim().length >= 3 ? findFlat(d.flats, raw) : undefined;
  const ready = !!flat && isOpen(flat) && isHandedOver(d);
  const error =
    raw.trim().length < 3
      ? undefined
      : !flat
        ? t("captain.manual.notFound")
        : !isOpen(flat)
          ? t("captain.flats.alreadyDone", { flat: flat.flat_label })
          : undefined;

  return (
    <div className="flex flex-1 flex-col gap-3.5">
      <FieldHeader
        back={{ label: t("captain.manual.back"), onPress: toFlats }}
        eyebrow={t("captain.manual.why")}
        title={t("captain.manual.title")}
      />
      <TextField
        label={t("captain.manual.field")}
        placeholder="B-402"
        description={t("captain.manual.help")}
        autoComplete="off"
        value={raw}
        onChange={setRaw}
        isInvalid={!!error}
        errorMessage={error}
      />
      {flat && !error && (
        <section className="flex flex-col gap-2 rounded-md bg-primary-soft p-3.5">
          <span className="text-[15px] leading-[22px]">{t("captain.manual.found")}</span>
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">
            {t("captain.manual.match", { flat: flat.flat_label })}
          </h2>
          <span className="text-[15px] leading-[22px]">
            {t("captain.manual.matchDetail", {
              contents: t(`captain.flats.contents.${flat.kind}`),
              society: d.society.name,
            })}
          </span>
        </section>
      )}
      <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
        {t("captain.manual.check")}
      </p>
      <div className="mt-auto">
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={!ready}
          onPress={() => {
            if (!flat) return;
            write(proof(flat));
            toast(t("captain.manual.done", { flat: flat.flat_label }));
            toFlats();
          }}
        >
          {flat
            ? t("captain.manual.confirm", { flat: flat.flat_label })
            : t("captain.manual.confirmAny")}
        </Button>
      </div>
    </div>
  );
}
