import { Button, Skeleton, TextField, toast } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { isOpen, proof } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainWrite } from "@/features/captain/queries.js";
import { normalizeCode } from "@/lib/scan.js";

/** A torn or smudged label: type the bag code, check the flat, hand it over (design CaptainManual; M7). */
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
  const code = normalizeCode(raw);
  const bag = code ? d.bags.find((b) => b.code === code) : undefined;
  const ready = !!bag && isOpen(bag) && !!d.stop.handed_over_at;
  const error =
    code.length < 3
      ? undefined
      : !bag
        ? t("captain.manual.notFound")
        : !isOpen(bag)
          ? t("captain.flats.alreadyDone", { code, flat: bag.flat })
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
        placeholder="B-214"
        description={t("captain.manual.help")}
        autoComplete="off"
        value={raw}
        onChange={setRaw}
        isInvalid={!!error}
        errorMessage={error}
      />
      {bag && !error && (
        <section className="flex flex-col gap-2 rounded-md bg-primary-soft p-3.5">
          <span className="text-[15px] leading-[22px]">{t("captain.manual.found")}</span>
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">
            {t("captain.manual.match", { code: bag.code, flat: bag.flat })}
          </h2>
          <span className="text-[15px] leading-[22px]">
            {t("captain.manual.matchDetail", {
              contents: t(`captain.flats.contents.${bag.contents}`),
              lines: bag.lines,
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
            if (!bag) return;
            write(proof(bag, true));
            toast(t("captain.manual.done", { code: bag.code, flat: bag.flat }));
            toFlats();
          }}
        >
          {bag ? t("captain.manual.confirm", { flat: bag.flat }) : t("captain.manual.confirmAny")}
        </Button>
      </div>
    </div>
  );
}
