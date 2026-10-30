import {
  Button,
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
import {
  useDeliveryPlace,
  useOrder,
  useOrders,
  useRateTiffin,
  useRating,
  useTiffinInfo,
} from "@/features/orders/queries.js";
import { shortDate, weekdayOf } from "@/features/subscriptions/format.js";

/** "How was today?": rate the home-cooked tiffin that came (design HcRate; M8 §3.5 rates tiffins only, no box and no tags). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/orders_/$orderId/rate")({
  component: Rate,
});

const STARS = ["1", "2", "3", "4", "5"].map((v) => ({ value: v, label: v }));

/**
 * One thing to rate: 1 to 5.
 * @param {{ title: string, by?: string, score: string, onScore: (v: string) => void }} props
 */
function RateCard({ title, by, score, onScore }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2.5 rounded-md bg-surface px-4 py-3.5">
      <h2 className="text-lg leading-[26px] font-bold">{title}</h2>
      {by && <span className="-mt-2 text-sm leading-5 text-fg-secondary">{by}</span>}
      <div>
        <SegmentedControl label={title} options={STARS} value={score} onChange={onScore} />
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
  const place = useDeliveryPlace();
  const rate = useRateTiffin();
  const [score, setScore] = useState("");
  const [comment, setComment] = useState("");
  const [reporting, setReporting] = useState(false);
  const o = order.data;
  // What is rated is the tiffin: this order, or the one that came with the box the same day.
  const tiffin =
    o &&
    (o.kind === "tiffin"
      ? o
      : (list.data ?? []).find(
          (x) =>
            x.kind === "tiffin" && x.delivery_date === o.delivery_date && x.status === "delivered",
        ));
  const tiffinRating = useRating(tiffin?.id);
  const tiffinInfo = useTiffinInfo(tiffin?.id, lang);

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

  if (order.isPending || list.isPending || tiffinRating.isLoading)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={10} />
      </div>
    );
  if (order.isError || list.isError || tiffinRating.isError || !o)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError
          onRetry={() => {
            order.refetch();
            list.refetch();
            tiffinRating.refetch();
          }}
        />
      </div>
    );
  // Nothing to rate: not delivered yet, rated already, or a box that came without a tiffin.
  const empty = !tiffin
    ? o.status === "delivered"
      ? "none"
      : "notYet"
    : tiffin.status !== "delivered"
      ? "notYet"
      : tiffinRating.data
        ? "done"
        : null;
  if (empty || !tiffin)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <EmptyState
          illustration={<TiffinSteam className="h-auto w-28" />}
          title={t(`rate.${empty}Title`)}
          body={t(`rate.${empty}Body`)}
          action={t("rate.backToOrders")}
          onAction={() => navigate({ to: "/orders" })}
        />
      </div>
    );

  const cook = tiffinInfo.data?.cook.name;
  const captain = place.captain;
  const send = () => {
    rate.mutate(
      { orderId: tiffin.id, score: Number(score), comment: comment.trim() },
      {
        onSuccess: () => {
          toast(t("rate.thanks"));
          navigate({ to: "/orders" });
        },
        onError: () => toast(t("rate.failed")),
      },
    );
  };

  return (
    <div className="flex min-h-[calc(100dvh-180px)] flex-col gap-3">
      {header}
      <RateCard
        title={t("rate.tiffin", { slot: tiffinInfo.data?.meal ?? "lunch" })}
        by={
          tiffinInfo.data
            ? t("rate.tiffinBy", {
                dish: tiffinInfo.data.dish.name,
                cook: tiffinInfo.data.cook.name,
              })
            : undefined
        }
        score={score}
        onScore={setScore}
      />
      <TextField
        label={t("rate.more")}
        placeholder={t("rate.morePh")}
        value={comment}
        onChange={setComment}
        maxLength={300}
      />
      <p className="text-sm leading-5 text-fg-secondary">
        {captain
          ? t("rate.privacy", { cook: cook ?? "", captain })
          : t("rate.privacyNoCaptain", { cook: cook ?? "" })}
      </p>
      <div className="mt-auto flex flex-col">
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={!score}
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
