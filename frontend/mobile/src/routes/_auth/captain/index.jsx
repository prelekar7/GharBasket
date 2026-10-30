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
import { ScreenError } from "@/components/ScreenError.jsx";
import { currentStop, isHandedOver, tally, tiffinStop } from "@/features/captain/day.js";
import { clock, dayLabel, ReceivedCard } from "@/features/captain/parts.jsx";
import { useCaptainDay, useCaptainPartner, useDrafts } from "@/features/captain/queries.js";
import { clockTime } from "@/features/subscriptions/format.js";
import { HOME_BY_ROLE, uiRole, useSession } from "@/lib/session.js";

/** Captain's morning at a glance: when the van comes, what it brings, how far along (design CaptainToday; M7). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/")({ component: Today });

function Today() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const { roles, setActiveRole } = useSession();
  const day = useCaptainDay();
  const partner = useCaptainPartner();
  const [drafts] = useDrafts(day.data?.date);

  if (day.isPending || partner.isPending) return <Skeleton lines={8} />;
  if (day.isError || !day.data) return <ScreenError onRetry={() => day.refetch()} />;
  const d = day.data;
  const name = partner.data?.display_name ?? "";
  const stop = currentStop(d);
  const handedOver = !!stop && isHandedOver(d);
  const n = tally(d);
  if (isHandedOver(d) && n.handled === n.total) return <AllDone day={d} name={name} />;
  const issues = Object.keys(drafts.issues).length;
  const sum = (/** @type {"crates" | "bags"} */ k) =>
    d.stops.filter((s) => s !== tiffin).reduce((total, s) => total + s.expected[k], 0);
  const tiffin = tiffinStop(d);
  const tiffins = d.flats.filter((f) => f.kind === "tiffin");
  const tiffinsToCount = tiffins.filter((f) => f.status === "on_the_way").length;
  /** @type {[string, number][]} */
  const rows = [
    [t("captain.today.crates"), sum("crates")],
    [t("captain.today.bags"), sum("bags")],
    [t("captain.today.held"), n.held],
  ];

  return (
    <div className="flex flex-1 flex-col gap-3">
      <header className="flex flex-col gap-2">
        {roles.length > 1 && (
          <RoleSwitcher
            roles={roles.map(uiRole)}
            active="captain"
            roleNames={
              /** @type {Record<string, string>} */ (
                Object.fromEntries(roles.map((r) => [uiRole(r), t(`role.${r}`)]))
              )
            }
            labels={{ actingAs: t("captain.actingAs"), role: t("captain.role") }}
            onChange={(picked) => {
              const role = roles.find((r) => uiRole(r) === picked);
              if (!role) return;
              setActiveRole(role);
              navigate({ to: HOME_BY_ROLE[role] });
            }}
          />
        )}
        <div className="flex flex-col">
          <span className="text-[15px] leading-[22px] text-fg-secondary">
            {t("captain.hello", { name, society: d.society.name })}
          </span>
          <h1 className="m-0 text-2xl leading-[30px] font-bold">
            {t("captain.today.title", { day: dayLabel(d.date, lang) })}
          </h1>
        </div>
      </header>
      {handedOver ? (
        <ReceivedCard />
      ) : (
        stop && (
          <section className="flex flex-col gap-1.5 rounded-md bg-accent-soft p-3.5">
            <h2 className="m-0 text-[19px] leading-[26px] font-bold">
              {t("captain.today.vanTitle", {
                time: stop?.eta_now ? clockTime(stop.eta_now, lang) : (stop?.slot.start ?? ""),
              })}
            </h2>
            <p className="m-0">{t("captain.today.van", { count: stop?.expected.crates ?? 0 })}</p>
          </section>
        )
      )}
      {tiffin && (
        <section className="flex flex-col gap-2 rounded-md bg-surface p-3.5">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">
            {t("captain.today.tiffinTitle", {
              time: tiffin.eta_now ? clockTime(tiffin.eta_now, lang) : (tiffin.slot?.start ?? ""),
            })}
          </h2>
          <p className="m-0">
            {t(tiffinsToCount ? "captain.today.tiffinBody" : "captain.today.tiffinCounted", {
              count: tiffins.length,
            })}
          </p>
          {tiffinsToCount > 0 && (
            <Button
              size="lg"
              block
              onPress={() => navigate({ to: "/captain/scan", search: { tiffin: true } })}
            >
              {t("captain.today.tiffinCount")}
            </Button>
          )}
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
        <Button
          variant="primary"
          size="lg"
          block
          onPress={() => navigate({ to: handedOver || !stop ? "/captain/flats" : "/captain/scan" })}
        >
          {handedOver || !stop ? t("captain.received.toFlats") : t("captain.today.receive")}
        </Button>
      </div>
    </div>
  );
}

/** Every bag delivered, held for its family or reported (design CaptainDone). @param {{ day: import("@/features/captain/day.js").CaptainDay, name: string }} props */
function AllDone({ day: d, name }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const n = tally(d);
  const problems = n.problems.map((f) => f.flat_label).join(", ");
  /** @type {[string, number][]} */
  const rows = [
    [t("captain.done.door"), n.door],
    [t("captain.done.held"), n.held],
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
        title={t("captain.done.doneTitle", { name })}
        body={t("captain.done.body")}
      />
      <Rows rows={rows} />
      <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
        {t("captain.done.heldNote")}
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
