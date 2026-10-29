import { Button, EmptyState, IconButton, Sheet, Skeleton, Switch, toast } from "@gharbasket/ui";
import { ArrowLeftIcon, GearSixIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import {
  useInbox,
  useMarkAllRead,
  useMarkRead,
  usePrefs,
  useSavePrefs,
} from "@/features/notifications/queries.js";
import { clockTime, istClock, shortDate } from "@/features/subscriptions/format.js";

/** Notifications: the in-app inbox and what you want to hear about, where (M11 screens). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/notifications")({
  component: Notifications,
});

function Notifications() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const navigate = useNavigate();
  const inbox = useInbox(lang);
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  const [settings, setSettings] = useState(false);

  return (
    <div className="flex flex-col gap-4">
      <header className="-ml-3 flex items-center justify-between gap-1">
        <div className="flex items-center gap-1">
          <IconButton
            label={t("onboarding.back")}
            variant="quiet"
            onPress={() => router.history.back()}
          >
            <ArrowLeftIcon size={24} />
          </IconButton>
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            {t("notif.title")}
          </h1>
        </div>
        <IconButton label={t("notif.settings")} variant="quiet" onPress={() => setSettings(true)}>
          <GearSixIcon size={24} />
        </IconButton>
      </header>
      {inbox.isPending ? (
        <Skeleton lines={6} />
      ) : inbox.isError || !inbox.data ? (
        <ScreenError onRetry={() => inbox.refetch()} />
      ) : inbox.data.items.length === 0 ? (
        <EmptyState title={t("notif.emptyTitle")} body={t("notif.emptyBody")} />
      ) : (
        <>
          {inbox.data.unread > 0 && (
            <div className="-mb-2 flex justify-end">
              <Button
                variant="quiet"
                onPress={() =>
                  markAll.mutate(undefined, { onError: () => toast(t("notif.markAllFailed")) })
                }
              >
                {t("notif.markAll")}
              </Button>
            </div>
          )}
          <ul>
            {inbox.data.items.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => {
                    if (!m.read_at) markRead.mutate(m.id);
                    navigate({ to: m.link });
                  }}
                  className="flex w-full gap-3 border-b border-line py-3 text-left outline-none focus-visible:shadow-focus"
                >
                  <span
                    aria-hidden
                    className={`mt-2 size-2 shrink-0 rounded-full ${m.read_at ? "bg-transparent" : "bg-primary"}`}
                  />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className={m.read_at ? "" : "font-semibold"}>
                      {m.title}
                      {!m.read_at && <span className="sr-only"> · {t("notif.unread")}</span>}
                    </span>
                    <span className="text-sm leading-5 text-fg-secondary">{m.body}</span>
                    <span className="text-xs leading-[18px] text-fg-secondary">
                      {shortDate(m.created_at.slice(0, 10), lang)} ·{" "}
                      {clockTime(istClock(m.created_at), lang)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      <PrefsSheet open={settings} onClose={() => setSettings(false)} />
    </div>
  );
}

/** Per category: push and WhatsApp. Quiet hours are fixed at night (M11 US-4, US-5). @param {{ open: boolean, onClose: () => void }} props */
function PrefsSheet({ open, onClose }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const prefs = usePrefs();
  const save = useSavePrefs();
  const [draft, setDraft] = useState(prefs.data?.categories ?? []);
  useEffect(() => {
    if (open && prefs.data) setDraft(prefs.data.categories);
  }, [open, prefs.data]);
  const set = (
    /** @type {string} */ category,
    /** @type {"push" | "whatsapp"} */ ch,
    /** @type {boolean} */ on,
  ) => setDraft((d) => d.map((c) => (c.category === category ? { ...c, [ch]: on } : c)));

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={t("notif.settings")}
      description={
        prefs.data
          ? t("notif.quiet", {
              from: clockTime(prefs.data.quiet_start, lang),
              to: clockTime(prefs.data.quiet_end, lang),
            })
          : undefined
      }
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isPending={save.isPending}
          onPress={() =>
            prefs.data &&
            save.mutate(
              { ...prefs.data, categories: draft },
              {
                onSuccess: () => {
                  onClose();
                  toast(t("notif.saved"));
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
      {prefs.isPending ? (
        <Skeleton lines={5} />
      ) : (
        <div className="flex flex-col gap-3">
          {draft.map((c) => (
            <fieldset
              key={c.category}
              className="m-0 flex flex-col border-0 border-b border-line p-0 pb-2"
            >
              <legend className="mb-1 p-0 font-semibold">{t(`notif.cat.${c.category}`)}</legend>
              <Switch
                label={t("notif.push")}
                isSelected={c.push}
                onChange={(on) => set(c.category, "push", on)}
              />
              <Switch
                label={t("notif.whatsapp")}
                description={t("notif.whatsappNote")}
                isSelected={c.whatsapp}
                onChange={(on) => set(c.category, "whatsapp", on)}
              />
            </fieldset>
          ))}
        </div>
      )}
    </Sheet>
  );
}
