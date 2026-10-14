import {
  Button,
  Dialog,
  formatPaise,
  RadioCards,
  Select,
  Sheet,
  Skeleton,
  Switch,
  TextField,
  toast,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMandate, useWallet } from "@/features/billing/queries.js";
import { useSociety } from "@/features/community/queries.js";
import {
  useChangeAddress,
  useDataExport,
  useDeleteAccount,
  useDevices,
  useMyHousehold,
  usePause,
  useRevokeDevice,
  useUpdateMe,
} from "@/features/identity/profile.js";
import { useInbox } from "@/features/notifications/queries.js";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { shortDate, todayIst } from "@/features/subscriptions/format.js";
import { LOCALES } from "@/i18n.js";
import { useSession } from "@/lib/session.js";

/** Profile: who you are, senior mode, and the way to everything about your account (design HcProfile, M1). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/profile")({ component: Profile });

const LANGUAGE_NAMES = { "en-IN": "English", "hi-IN": "हिन्दी", "mr-IN": "मराठी" };
/** @typedef {"language" | "household" | "address" | "autopilot" | "devices" | "privacy" | null} SheetName */

/** Household parts Profile can change, each on its onboarding screen in edit mode; Budget Lock has its own screen. */
const HOUSEHOLD_STEPS_EDIT = [
  { key: "family", to: "/onboarding/family" },
  { key: "food", to: "/onboarding/food" },
  { key: "health", to: "/onboarding/health" },
  { key: "cooking", to: "/onboarding/cooking" },
  { key: "budget", to: "/budget" },
];

/** "YYYY-MM-DD", `weeks` weeks from today in India. @param {string} weeks */
function addWeeks(weeks) {
  const d = new Date(`${todayIst()}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7 * Number(weeks));
  return d.toISOString().slice(0, 10);
}

function Profile() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = /** @type {keyof typeof LANGUAGE_NAMES} */ (i18n.resolvedLanguage ?? "en-IN");
  const { user, roles, signOut } = useSession();
  const household = useMyHousehold();
  const { data: society } = useSociety(household.data?.society_id);
  const wallet = useWallet();
  const mandate = useMandate();
  const update = useUpdateMe();
  const pause = usePause(household.data?.subscription.id);
  const inbox = useInbox(lang);
  const changeAddress = useChangeAddress(household.data?.address_id);
  const [tower, setTower] = useState("");
  const [flatNo, setFlatNo] = useState("");
  const startEdit = useOnboardingDraft((d) => d.startEdit);
  const [weeks, setWeeks] = useState("2");
  const hh = household.data;
  const paused = hh?.subscription.status === "paused";
  const pausedUntil = household.data?.subscription.paused_until
    ? shortDate(household.data.subscription.paused_until, lang)
    : "";
  /** The current setting, in words, for each editable part. @param {string} key @param {import("@/features/identity/profile.js").Household} h */
  const householdValue = (key, h) =>
    key === "family"
      ? t("profile.people", { n: h.size })
      : key === "food"
        ? t(`onboarding.food.diets.${h.diet}`)
        : key === "health"
          ? t(h.health_consent ? "profile.edit.on" : "profile.edit.off")
          : key === "cooking"
            ? t("profile.edit.minutes", { n: h.weekday_cook_minutes })
            : h.budget_paise
              ? formatPaise(h.budget_paise)
              : t("profile.edit.off");
  const [sheet, setSheet] = useState(/** @type {SheetName} */ (null));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const devices = useDevices();
  const revoke = useRevokeDevice();
  const exportData = useDataExport();
  const deleteAccount = useDeleteAccount();

  const senior = !!user?.senior_mode;
  const leave = async (/** @type {boolean} */ everywhere) => {
    await signOut({ everywhere });
    navigate({ to: "/login" });
  };
  const where = [household.data?.flat, society?.name].filter(Boolean).join(", ");
  const phone = user?.phone_e164.replace(/^\+91(\d{5})(\d{5})$/, "+91 $1 $2") ?? "";

  /** @type {{ key: string, label: string, value?: string, onPress: () => void }[]} */
  const rows = [
    {
      key: "language",
      label: t("profile.language"),
      value: LANGUAGE_NAMES[lang],
      onPress: () => setSheet("language"),
    },
    {
      key: "household",
      label: t("profile.household"),
      value: household.data ? t("profile.people", { n: household.data.size }) : undefined,
      onPress: () => setSheet("household"),
    },
    {
      key: "address",
      label: t("profile.address.row"),
      value: where || undefined,
      onPress: () => {
        const [w = "", n = ""] = (household.data?.flat ?? "").split("-");
        setTower(w.toLowerCase());
        setFlatNo(n);
        setSheet("address");
      },
    },
    {
      key: "autopilot",
      label: t("profile.pause.row"),
      value: household.data
        ? paused
          ? t("profile.pause.pausedValue", { date: pausedUntil })
          : t("profile.pause.on")
        : undefined,
      onPress: () => setSheet("autopilot"),
    },
    {
      key: "wallet",
      label: t("wallet.title"),
      value: wallet.data ? formatPaise(wallet.data.balance_paise) : undefined,
      onPress: () => navigate({ to: "/wallet" }),
    },
    {
      key: "budget",
      label: t("budget.title"),
      value: hh
        ? hh.budget_paise
          ? formatPaise(hh.budget_paise)
          : t("profile.edit.off")
        : undefined,
      onPress: () => navigate({ to: "/budget" }),
    },
    {
      key: "autopay",
      label: t("autopay.title"),
      value: mandate.data ? t("autopay.on") : undefined,
      onPress: () => navigate({ to: "/autopay" }),
    },
    ...(society
      ? [
          {
            key: "society",
            label: t("society.title"),
            value: society.name,
            onPress: () =>
              navigate({ to: "/society/$societyId", params: { societyId: society.id } }),
          },
        ]
      : []),
    {
      key: "notifications",
      label: t("notif.title"),
      value: inbox.data?.unread ? t("notif.unreadCount", { n: inbox.data.unread }) : undefined,
      onPress: () => navigate({ to: "/notifications" }),
    },
    {
      key: "devices",
      label: t("profile.devices"),
      value: devices.data ? String(devices.data.length) : undefined,
      onPress: () => setSheet("devices"),
    },
    {
      key: "privacy",
      label: t("profile.privacy"),
      value: t("profile.privacyValue"),
      onPress: () => setSheet("privacy"),
    },
    {
      key: "partner",
      label: t("profile.partner"),
      onPress: () => navigate({ to: "/partner" }),
    },
    ...(roles.length > 1
      ? [{ key: "role", label: t("profile.switchRole"), onPress: () => navigate({ to: "/role" }) }]
      : []),
  ];

  return (
    <div className="flex flex-col gap-2.5">
      <header className="flex flex-col">
        {senior && (
          <span className="text-sm leading-6 font-semibold text-fg-secondary">
            {t("profile.seniorOn")}
          </span>
        )}
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("tabs.profile")}
        </h1>
      </header>
      <section className="flex flex-col rounded-md bg-surface px-4 py-3">
        {household.isPending ? (
          <Skeleton lines={2} />
        ) : (
          <>
            <strong>{[user?.name, where].filter(Boolean).join(" · ") || phone}</strong>
            <span className="text-sm text-fg-secondary">
              {[phone, household.data && t("profile.family", { n: household.data.size })]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </>
        )}
      </section>
      <div className="border-b border-line [&>label]:max-w-none">
        <Switch
          label={t("profile.senior")}
          description={t("profile.seniorDesc")}
          isSelected={senior}
          onChange={(on) =>
            update.mutate({ senior_mode: on }, { onError: () => toast(t("profile.saveFailed")) })
          }
        />
      </div>
      <ul className="-mt-2.5">
        {rows.map((r) => (
          <li key={r.key}>
            <button
              type="button"
              onClick={r.onPress}
              className="flex min-h-14 w-full items-center justify-between gap-3 border-b border-line py-3 text-left outline-none focus-visible:shadow-focus [html[data-senior]_&]:min-h-16"
            >
              <span>{r.label}</span>
              <span className="text-sm whitespace-nowrap text-fg-secondary">
                {r.value ? `${r.value} ` : ""}
                <span aria-hidden>›</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <Button variant="quiet" block onPress={() => leave(false)}>
        {t("profile.signOut")}
      </Button>

      <Sheet
        open={sheet === "language"}
        onOpenChange={(o) => setSheet(o ? "language" : null)}
        title={t("profile.language")}
      >
        <RadioCards
          label={t("profile.language")}
          value={lang}
          onChange={(lng) => {
            i18n.changeLanguage(lng);
            update.mutate({ locale: /** @type {typeof lang} */ (lng) });
            setSheet(null);
          }}
          options={LOCALES.map((value) => ({ value, title: LANGUAGE_NAMES[value] }))}
        />
      </Sheet>

      <Sheet
        open={sheet === "household"}
        onOpenChange={(o) => setSheet(o ? "household" : null)}
        title={t("profile.household")}
        description={t("profile.edit.lead")}
      >
        {hh && (
          <ul>
            {HOUSEHOLD_STEPS_EDIT.map(({ key, to }) => (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => {
                    if (key !== "budget") startEdit(hh);
                    setSheet(null);
                    navigate({ to });
                  }}
                  className="flex min-h-14 w-full items-center justify-between gap-3 border-b border-line py-3 text-left outline-none focus-visible:shadow-focus"
                >
                  <span>{t(`profile.edit.${key}`)}</span>
                  <span className="text-sm whitespace-nowrap text-fg-secondary">
                    {householdValue(key, hh)} <span aria-hidden>›</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Sheet>

      <Sheet
        open={sheet === "address"}
        onOpenChange={(o) => setSheet(o ? "address" : null)}
        title={t("profile.address.title")}
        description={t("profile.address.lead", { society: society?.name ?? "" })}
        footer={
          <Button
            variant="primary"
            size="lg"
            block
            isPending={changeAddress.isPending}
            isDisabled={!tower || !flatNo.trim()}
            onPress={() =>
              changeAddress.mutate(
                { tower, flat: flatNo.trim() },
                {
                  onSuccess: () => {
                    setSheet(null);
                    toast(t("profile.address.saved"));
                  },
                  onError: () => toast(t("profile.saveFailed")),
                },
              )
            }
          >
            {t("profile.edit.save")}
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <Select
            label={t("onboarding.flat.tower")}
            placeholder={t("onboarding.flat.towerPh")}
            value={tower}
            onChange={setTower}
            options={(society?.towers ?? []).map((tw) => ({ value: tw.id, label: tw.label }))}
          />
          <TextField
            label={t("onboarding.flat.flat")}
            inputMode="numeric"
            autoComplete="off"
            value={flatNo}
            onChange={(v) => setFlatNo(v.replace(/[^0-9A-Za-z]/g, "").slice(0, 6))}
          />
          <p className="text-sm leading-5 text-fg-secondary">{t("profile.address.move")}</p>
        </div>
      </Sheet>

      <Sheet
        open={sheet === "autopilot"}
        onOpenChange={(o) => setSheet(o ? "autopilot" : null)}
        title={t("profile.pause.title")}
        description={t(paused ? "profile.pause.pausedLead" : "profile.pause.lead", {
          date: pausedUntil,
        })}
        footer={
          paused ? (
            <Button
              variant="primary"
              size="lg"
              block
              isPending={pause.isPending}
              onPress={() =>
                pause.mutate(null, {
                  onSuccess: () => {
                    setSheet(null);
                    toast(t("profile.pause.resumed"));
                  },
                  onError: () => toast(t("profile.saveFailed")),
                })
              }
            >
              {t("profile.pause.resume")}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="lg"
              block
              isPending={pause.isPending}
              onPress={() =>
                pause.mutate(addWeeks(weeks), {
                  onSuccess: (sub) => {
                    setSheet(null);
                    toast(
                      t("profile.pause.paused", { date: shortDate(sub.paused_until ?? "", lang) }),
                    );
                  },
                  onError: () => toast(t("profile.saveFailed")),
                })
              }
            >
              {t("profile.pause.cta", { n: Number(weeks) })}
            </Button>
          )
        }
      >
        {!paused && (
          <RadioCards
            label={t("profile.pause.title")}
            value={weeks}
            onChange={setWeeks}
            options={["1", "2", "4"].map((w) => ({
              value: w,
              title: t("profile.pause.weeks", { n: Number(w) }),
              description: t("profile.pause.until", { date: shortDate(addWeeks(w), lang) }),
            }))}
          />
        )}
      </Sheet>

      <Sheet
        open={sheet === "devices"}
        onOpenChange={(o) => setSheet(o ? "devices" : null)}
        title={t("profile.devices")}
        footer={
          <Button variant="secondary" block onPress={() => leave(true)}>
            {t("profile.signOutAll")}
          </Button>
        }
      >
        {devices.isPending ? (
          <Skeleton lines={3} />
        ) : (
          <ul>
            {devices.data?.map((d) => (
              <li
                key={d.id}
                className="flex items-center justify-between gap-3 border-b border-line py-2.5"
              >
                <span className="flex flex-col">
                  <span className="font-semibold">
                    {d.current ? t("profile.thisPhone") : d.label}
                  </span>
                  <span className="text-sm text-fg-secondary">
                    {d.current
                      ? t("profile.activeNow")
                      : t("profile.lastSeen", {
                          date: shortDate(d.last_seen_at.slice(0, 10), lang),
                        })}
                  </span>
                </span>
                {!d.current && (
                  <Button
                    variant="quiet"
                    isPending={revoke.isPending && revoke.variables === d.id}
                    onPress={() =>
                      revoke.mutate(d.id, {
                        onSuccess: () => toast(t("profile.revoked", { device: d.label })),
                        onError: () => toast(t("profile.saveFailed")),
                      })
                    }
                  >
                    {t("profile.signOutDevice")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Sheet>

      <Sheet
        open={sheet === "privacy"}
        onOpenChange={(o) => setSheet(o ? "privacy" : null)}
        title={t("profile.privacy")}
        description={t("profile.privacyLead")}
      >
        <div className="flex flex-col gap-3">
          <Button
            variant="secondary"
            block
            isPending={exportData.isPending}
            onPress={() =>
              exportData.mutate(undefined, {
                onSuccess: (r) => {
                  setSheet(null);
                  toast(t("profile.exportQueued", { hours: r.ready_within_hours }));
                },
                onError: () => toast(t("profile.saveFailed")),
              })
            }
          >
            {t("profile.export")}
          </Button>
          <Button variant="danger" block onPress={() => setConfirmDelete(true)}>
            {t("profile.delete")}
          </Button>
        </div>
      </Sheet>

      <Dialog
        title={t("profile.deleteTitle")}
        confirmLabel={t("profile.deleteConfirm")}
        cancelLabel={t("profile.keep")}
        tone="danger"
        isOpen={confirmDelete}
        onOpenChange={setConfirmDelete}
        onConfirm={() =>
          deleteAccount.mutate(undefined, {
            onSuccess: async (r) => {
              toast(t("profile.deleted", { date: shortDate(r.purge_on, lang) }));
              await leave(false);
            },
            onError: () => toast(t("profile.saveFailed")),
          })
        }
      >
        {t("profile.deleteBody")}
      </Dialog>
    </div>
  );
}
