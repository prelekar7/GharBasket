import {
  Badge,
  Button,
  Chip,
  EmptyState,
  GullakCoin,
  QtyStepper,
  Skeleton,
  toast,
} from "@gharbasket/ui";
import { PlusIcon } from "@phosphor-icons/react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { KindnessGullak } from "@/features/billing/KindnessGullak.jsx";
import { useKindness } from "@/features/billing/queries.js";
import {
  describeItems,
  useDonations,
  useDrives,
  usePointRates,
  useScheduleDonation,
} from "@/features/community/kindness.js";
import { shortDate, weekdayName } from "@/features/subscriptions/format.js";

/** Kindness: your gullak, give things away on the van's way back, what you gave (design HcKindness, M9, W8). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/kindness")({ component: Kindness });

const MAX_PHOTOS = 4;

function Kindness() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const balance = useKindness();
  const drives = useDrives();
  const rates = usePointRates();
  const donations = useDonations();
  const schedule = useScheduleDonation();
  // What is being given: code → quantity (kg for clothes and utensils, pieces for the rest).
  const [picked, setPicked] = useState(/** @type {Record<string, number>} */ ({}));
  const [photos, setPhotos] = useState(/** @type {File[]} */ ([]));
  const fileInput = useRef(/** @type {HTMLInputElement | null} */ (null));

  const header = (
    <header className="flex flex-col">
      <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
        {t("tabs.kindness")}
      </span>
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">
        {t("kindness.title")}
      </h1>
    </header>
  );
  if (balance.isPending || donations.isPending)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  if (balance.isError || donations.isError || !balance.data || !donations.data)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError
          onRetry={() => {
            balance.refetch();
            donations.refetch();
          }}
        />
      </div>
    );

  const k = balance.data;
  // The running drive with the highest multiplier is suggested (M9 §3.4); it applies to the
  // lines whose code it multiplies.
  const drive = [...(drives.data ?? [])]
    .filter((d) => d.state === "running")
    .sort((a, b) => b.multiplier - a.multiplier)[0];
  const pickupOn = donations.data.pickup_dates[0];
  const day = (/** @type {string} */ iso) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;
  /** @param {import("@/features/community/kindness.js").Donation} d */
  const describe = (d) => describeItems(t, lang, d.items);
  const codes = Object.keys(picked);
  const toggle = (/** @type {string} */ code, /** @type {boolean} */ on) =>
    setPicked((p) => {
      const { [code]: _gone, ...rest } = p;
      return on ? { ...p, [code]: 1 } : rest;
    });

  return (
    <div className="flex flex-col gap-3">
      {header}
      <KindnessGullak balance={k} canDonate />
      <p className="text-sm leading-[22px] text-fg-secondary">
        {k.expiring[0]
          ? t("kindness.pointsNote", {
              points: k.expiring[0].points,
              date: shortDate(k.expiring[0].on, lang),
            })
          : t("kindness.pointsNoteNoExpiry")}
      </p>
      <Link
        to="/kindness/impact"
        className="-mt-1 flex min-h-12 items-center justify-between gap-3 border-y border-line outline-none focus-visible:shadow-focus"
      >
        <span>{t("impact.link")}</span>
        <span aria-hidden className="text-fg-secondary">
          ›
        </span>
      </Link>
      <section className="flex flex-col gap-2.5 rounded-md bg-surface px-4 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <strong>{t("kindness.give")}</strong>
          {drive && (
            <Badge tone="accent">
              {t("kindness.drive", { name: drive.name, x: drive.multiplier })}
            </Badge>
          )}
        </div>
        <p className="text-sm leading-5 text-fg-secondary">{t("kindness.how")}</p>
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">{t("kindness.what")}</legend>
          {(rates.data ?? []).map(({ code }) => (
            <Chip key={code} isSelected={code in picked} onChange={(on) => toggle(code, on)}>
              {t(`kindness.items.${code}`)}
            </Chip>
          ))}
        </fieldset>
        {(rates.data ?? [])
          .filter((r) => r.code in picked)
          .map((r) => (
            <div key={r.code} className="flex items-center justify-between gap-3">
              <span className="text-sm">{t(`kindness.items.${r.code}`)}</span>
              <QtyStepper
                label={t(`kindness.items.${r.code}`)}
                value={picked[r.code]}
                onChange={(qty) => setPicked((p) => ({ ...p, [r.code]: qty }))}
                min={1}
                max={r.max_qty}
                unit={r.unit === "kg" ? t("kindness.unitKg") : undefined}
                labels={{ decrease: t("box.qty.less"), increase: t("box.qty.more") }}
              />
            </div>
          ))}
        <input
          ref={fileInput}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => setPhotos((p) => [...p, ...(e.target.files ?? [])].slice(0, MAX_PHOTOS))}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="secondary"
            isDisabled={photos.length >= MAX_PHOTOS}
            onPress={() => fileInput.current?.click()}
          >
            <PlusIcon aria-hidden size={20} weight="bold" />
            {t("kindness.photos")}
          </Button>
          {photos.length > 0 && (
            <span className="text-sm text-fg-secondary">
              {t("kindness.photosAdded", { count: photos.length })}
            </span>
          )}
        </div>
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={codes.length === 0 || !pickupOn}
          isPending={schedule.isPending}
          onPress={() =>
            schedule.mutate(
              {
                items: codes.map((code) => ({ code, qty: picked[code] })),
                photos,
                driveId: drive?.codes.some((c) => c in picked) ? drive.id : undefined,
                pickupOn,
              },
              {
                onSuccess: () => {
                  setPicked({});
                  setPhotos([]);
                  toast(t("kindness.booked", { day: day(pickupOn) }));
                },
                onError: () => toast(t("kindness.failed")),
              },
            )
          }
        >
          {pickupOn ? t("kindness.cta", { day: day(pickupOn) }) : t("kindness.noPickupDay")}
        </Button>
      </section>
      {donations.data.items.length === 0 && (
        <EmptyState
          illustration={<GullakCoin className="h-auto w-28" />}
          title={t("kindness.emptyTitle")}
          body={t("kindness.emptyBody")}
        />
      )}
      {donations.data.items.length > 0 && (
        <ul aria-label={t("kindness.history")}>
          {donations.data.items.map((d) => (
            <li
              key={d.id}
              className="flex items-baseline justify-between gap-3 border-b border-line py-2.5 text-sm leading-5"
            >
              <span>
                {describe(d)} ·{" "}
                {t(`kindness.status.${d.status}`, {
                  day: day(d.pickup_on),
                  defaultValue: d.status,
                })}
              </span>
              {d.points_awarded != null && (
                <strong className="tabular-nums">+{d.points_awarded}</strong>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
