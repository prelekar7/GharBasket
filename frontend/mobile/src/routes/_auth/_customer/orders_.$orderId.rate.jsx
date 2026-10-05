import {
  Button,
  Chip,
  EmptyState,
  IconButton,
  SegmentedControl,
  Skeleton,
  TextField,
  TiffinSteam,
  toast,
} from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { ProblemSheet } from "@/features/orders/ProblemSheet.jsx";
import { useOrder, useOrders, useRateDelivery } from "@/features/orders/queries.js";
import { shortDate, weekdayOf } from "@/features/subscriptions/format.js";

/** "How was today?": rate the delivered box and the tiffin that came with it (design HcRate; M4, M8). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/orders_/$orderId/rate")({
  component: Rate,
});

const STARS = ["1", "2", "3", "4", "5"].map((v) => ({ value: v, label: v }));
const TAGS = {
  box: ["fresh", "on_time", "packed_well", "missing"],
  tiffin: ["tasty", "hot", "portion", "oily"],
};

/**
 * One thing to rate: 1 to 5 and a few chips.
 * @param {{ title: string, by?: string, kind: "box" | "tiffin", score: string, onScore: (v: string) => void, tags: string[], onTags: (update: (tags: string[]) => string[]) => void }} props
 */
function RateCard({ title, by, kind, score, onScore, tags, onTags }) {
  const { t } = useTranslation();
  return (
    <section aria-label={title} className="flex flex-col gap-2.5 rounded-md bg-surface px-4 py-3.5">
      <h2 className="text-lg leading-[26px] font-bold">{title}</h2>
      {by && <span className="-mt-2 text-sm leading-5 text-fg-secondary">{by}</span>}
      <div>
        <SegmentedControl label={title} options={STARS} value={score} onChange={onScore} />
      </div>
      <div className="flex flex-wrap gap-2">
        {TAGS[kind].map((tag) => (
          <Chip
            key={tag}
            isSelected={tags.includes(tag)}
            onChange={(on) => onTags((now) => (on ? [...now, tag] : now.filter((x) => x !== tag)))}
          >
            {t(`rate.tags.${kind}.${tag}`)}
          </Chip>
        ))}
      </div>
    </section>
  );
}

function Rate() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const navigate = useNavigate();
  const { orderId } = Route.useParams();
  const order = useOrder(orderId, lang);
  const list = useOrders(lang);
  const rate = useRateDelivery();
  const [boxScore, setBoxScore] = useState("");
  const [boxTags, setBoxTags] = useState(/** @type {string[]} */ ([]));
  const [tiffinScore, setTiffinScore] = useState("");
  const [tiffinTags, setTiffinTags] = useState(/** @type {string[]} */ ([]));
  const [comment, setComment] = useState("");
  const [reporting, setReporting] = useState(false);
  const o = order.data;

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
        {o && (
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("rate.when", {
              day: `${t(`days.short.d${weekdayOf(o.delivery_date)}`)} ${shortDate(o.delivery_date, lang)}`,
            })}
          </span>
        )}
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("rate.title")}</h1>
      </div>
    </header>
  );

  if (order.isPending || list.isPending)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={10} />
      </div>
    );
  if (order.isError || list.isError || !o)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError
          onRetry={() => {
            order.refetch();
            list.refetch();
          }}
        />
      </div>
    );
  if (o.status !== "DELIVERED" || o.rating)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <EmptyState
          illustration={<TiffinSteam className="h-auto w-28" />}
          title={t(o.rating ? "rate.doneTitle" : "rate.notYetTitle")}
          body={t(o.rating ? "rate.doneBody" : "rate.notYetBody")}
          action={t("rate.backToOrders")}
          onAction={() => navigate({ to: "/orders" })}
        />
      </div>
    );

  // The tiffin that came the same day, if there was one and it is not rated yet.
  const tiffin = (list.data ?? []).find(
    (x) =>
      x.kind === "tiffin" &&
      x.delivery_date === o.delivery_date &&
      x.status === "DELIVERED" &&
      !x.rating,
  );
  const captain = o.captain?.name ?? "";
  const send = () => {
    /** @type {import("@/features/orders/queries.js").Rating[]} */
    const ratings = [];
    if (boxScore) ratings.push({ orderId: o.id, score: Number(boxScore), tags: boxTags });
    if (tiffin && tiffinScore)
      ratings.push({ orderId: tiffin.id, score: Number(tiffinScore), tags: tiffinTags });
    // The words go with the first rating sent.
    if (ratings[0]) ratings[0].comment = comment.trim();
    rate.mutate(ratings, {
      onSuccess: () => {
        toast(t("rate.thanks"));
        navigate({ to: "/orders" });
      },
      onError: () => toast(t("rate.failed")),
    });
  };

  return (
    <div className="flex min-h-[calc(100dvh-180px)] flex-col gap-3">
      {header}
      <RateCard
        title={t("rate.box")}
        kind="box"
        score={boxScore}
        onScore={setBoxScore}
        tags={boxTags}
        onTags={setBoxTags}
      />
      {tiffin && (
        <RateCard
          title={t("rate.tiffin", { slot: tiffin.slot ?? "lunch" })}
          by={t("rate.tiffinBy", { dish: tiffin.dish?.name ?? "", cook: tiffin.cook?.name ?? "" })}
          kind="tiffin"
          score={tiffinScore}
          onScore={setTiffinScore}
          tags={tiffinTags}
          onTags={setTiffinTags}
        />
      )}
      <TextField
        label={t("rate.more")}
        placeholder={t("rate.morePh")}
        value={comment}
        onChange={setComment}
        maxLength={500}
      />
      <p className="text-sm leading-5 text-fg-secondary">
        {tiffin
          ? t("rate.privacy", { cook: tiffin.cook?.name ?? "", captain })
          : t("rate.privacyBox", { captain })}
      </p>
      <div className="mt-auto flex flex-col">
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={!boxScore && !(tiffin && tiffinScore)}
          isPending={rate.isPending}
          onPress={send}
        >
          {t("rate.send")}
        </Button>
        <Button variant="quiet" block onPress={() => setReporting(true)}>
          {t("rate.problem")}
        </Button>
      </div>
      <ProblemSheet orderId={o.id} open={reporting} onOpenChange={setReporting} />
    </div>
  );
}
