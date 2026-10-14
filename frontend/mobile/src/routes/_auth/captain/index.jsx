import {
  Button,
  EmptyBasket,
  EmptyState,
  ProgressBar,
  RoleSwitcher,
  Skeleton,
} from "@gharbasket/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScanField } from "@/components/ScanField.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { tally } from "@/features/captain/day.js";
import { clock, dayLabel, ReceivedCard, useCrates } from "@/features/captain/parts.jsx";
import { useCaptainDay, useDrafts } from "@/features/captain/queries.js";
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
  const [drafts] = useDrafts(day.data?.date);

  if (day.isPending) return <Skeleton lines={8} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const n = tally(d);
  if (d.stop.handed_over_at && n.handled === n.total) return <AllDone day={d} />;
  const issues = Object.keys(drafts.issues).length;
  const next = crates.find((c) => c.state === "waiting");
  /** @type {[string, number][]} */
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
      <Rows rows={rows} />
      <ProgressBar
        tone="accent"
        label={t("captain.today.delivered")}
        value={n.handled}
        max={n.total}
        valueLabel={t("captain.ofTotal", { done: n.handled, total: n.total })}
      />
      <Link
        to="/captain/flats/exceptions"
        className={`flex min-h-12 items-center text-[15px] leading-[22px] text-link ${issues ? "font-semibold underline underline-offset-[3px]" : ""}`}
      >
        {issues
          ? t("captain.exceptions.count", { count: issues })
          : t("captain.today.noExceptions")}
      </Link>
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

/** Every bag delivered, in the lobby or reported (design CaptainDone). @param {{ day: import("@/features/captain/day.js").CaptainDay }} props */
function AllDone({ day: d }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const n = tally(d);
  const problems = n.problems
    .map((b) => `${b.flat}, ${b.note ?? t(`captain.flats.status.${b.status}`)}`)
    .join("; ");
  /** @type {[string, number][]} */
  const rows = [
    [t("captain.done.door"), n.door],
    [t("captain.done.lobby"), n.lobby],
    [
      problems ? t("captain.done.problem", { list: problems }) : t("captain.done.problemNone"),
      n.problems.length,
    ],
  ];
  return (
    <div className="flex flex-1 flex-col gap-3">
      <FieldHeader
        eyebrow={t("captain.done.when", {
          society: d.society.name,
          day: dayLabel(d.date, lang),
          time: clock(new Date().toISOString(), lang),
        })}
        title={t("captain.done.title")}
      />
      <ProgressBar
        tone="accent"
        label={t("captain.done.handled")}
        value={n.handled}
        max={n.total}
        valueLabel={t("captain.ofTotal", { done: n.handled, total: n.total })}
      />
      <EmptyState
        illustration={<EmptyBasket />}
        title={t("captain.done.doneTitle", { name: d.captain_name })}
        body={t("captain.done.body")}
      />
      <Rows rows={rows} />
      <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
        {t("captain.done.lobbyNote")}
      </p>
      <div className="mt-auto">
        <Button block onPress={() => navigate({ to: "/captain/earnings" })}>
          {t("captain.done.earnings")}
        </Button>
      </div>
    </div>
  );
}

/** Label and count rows. @param {{ rows: [string, number][] }} props */
function Rows({ rows }) {
  return (
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
  );
}
