import { Button, ProgressBar, RoleSwitcher, Skeleton } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { tally } from "@/features/captain/day.js";
import { clock, dayLabel, ReceivedCard, useCrates } from "@/features/captain/parts.jsx";
import { useCaptainDay } from "@/features/captain/queries.js";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** Captain's morning at a glance: when the van comes, what it brings, how far along (design CaptainToday; M7). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/")({ component: Today });

function Today() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const { roles, setActiveRole } = useSession();
  const day = useCaptainDay();
  const { crates, receive } = useCrates(day.data);

  if (day.isPending) return <Skeleton lines={8} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const n = tally(d);
  const next = crates.find((c) => c.state === "waiting");
  const rows = [
    [t("captain.today.crates"), d.crates.length],
    [t("captain.today.bags"), n.total],
    [t("captain.today.lobby"), d.bags.filter((b) => b.pickup === "lobby").length],
  ];

  return (
    <div className="flex flex-1 flex-col gap-3">
      <header className="flex flex-col gap-2">
        {roles.length > 1 && (
          <RoleSwitcher
            roles={roles}
            active="captain"
            roleNames={
              /** @type {Record<import("@/lib/api.js").FieldRole, string>} */ (
                Object.fromEntries(roles.map((r) => [r, t(`role.${r}`)]))
              )
            }
            labels={{ actingAs: t("captain.actingAs"), role: t("captain.role") }}
            onChange={(role) => {
              setActiveRole(role);
              navigate({ to: HOME_BY_ROLE[role] });
            }}
          />
        )}
        <div className="flex flex-col">
          <span className="text-[15px] leading-[22px] text-fg-secondary">
            {t("captain.hello", { name: d.captain_name, society: d.society.name })}
          </span>
          <h1 className="m-0 text-2xl leading-[30px] font-bold">
            {t("captain.today.title", { day: dayLabel(d.date, lang) })}
          </h1>
        </div>
      </header>
      {d.stop.handed_over_at ? (
        <ReceivedCard day={d} />
      ) : (
        <section className="flex flex-col gap-1.5 rounded-md bg-accent-soft p-3.5">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">
            {t("captain.today.vanTitle", { time: clock(d.stop.eta, lang) })}
          </h2>
          <p className="m-0">
            {t("captain.today.van", {
              rider: d.stop.rider_name,
              count: d.crates.length,
              gate: d.stop.gate,
            })}
          </p>
        </section>
      )}
      <dl className="m-0">
        {rows.map(([label, value]) => (
          <div
            key={label}
            className="flex items-baseline justify-between gap-3 border-b border-line py-2.5"
          >
            <dt className="text-[15px] leading-[22px] text-fg-secondary">{label}</dt>
            <dd className="m-0 font-bold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <ProgressBar
        tone="accent"
        label={t("captain.today.delivered")}
        value={n.handled}
        max={n.total}
        valueLabel={t("captain.ofTotal", { done: n.handled, total: n.total })}
      />
      <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
        {t("captain.today.noExceptions")}
      </p>
      <div className="mt-auto flex flex-col">
        {d.stop.handed_over_at ? (
          <Button
            variant="primary"
            size="lg"
            block
            onPress={() => navigate({ to: "/captain/flats" })}
          >
            {t("captain.received.toFlats")}
          </Button>
        ) : (
          <ScanField
            label={t("captain.today.scan")}
            manualLabel={t("captain.manualCrate")}
            expected={next?.id}
            onCode={(code) => {
              receive(code);
              navigate({ to: "/captain/scan" });
            }}
            onManual={() => navigate({ to: "/captain/scan", search: { typing: true } })}
          />
        )}
      </div>
    </div>
  );
}
