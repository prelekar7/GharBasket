"use client";

import { Badge, Button, Select, Tabs, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { formatDate, newKey, plural } from "@/lib/fleet.js";
import { KIND_LABEL, reviewChecks, waitingDays } from "@/lib/partners.js";
import { Toasts } from "../_fleet/Toasts.jsx";
import { decideApplication, fileLinks } from "./actions.js";

const TABS = ["review", "docs", "active", "suspended"];
const CHECK_TONE = /** @type {const} */ ({ ok: "primary", look: "accent", missing: "danger" });

const GRADES = [
  { value: "A", label: "A · very good" },
  { value: "B", label: "B · good" },
  { value: "C", label: "C · needs work" },
];

/** @param {import("@/lib/partners.js").ApplicationReview} a */
const scopeText = (a) =>
  a.kind === "captain" && a.society
    ? `would be Society Captain of ${a.society.name}${a.society.has_active_captain ? " (it already has a captain)" : ""}`
    : a.kind === "kitchen"
      ? "would cook for this hub"
      : "would work at this hub";

/** @param {import("@/lib/partners.js").ApplicationReview} a */
const trainingDone = (a) => ({
  done: a.requirements.trainings.filter((t) => t.status === "passed").length,
  total: a.requirements.trainings.length,
});

/**
 * Partners and KYC: the review queue with a decision panel, and the hub's active and suspended partners.
 * @param {{ applications: import("@/lib/partners.js").ApplicationReview[], partners: import("@/lib/partners.js").PartnerStaff[], now: number }} props
 */
export function PartnersView({ applications, partners, now }) {
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("review").withOptions({ clearOnDefault: true }),
  );
  const [picked, setPicked] = useQueryState("applicant", parseAsString);

  // kyc_pending: every required document is in and the application waits for a decision; applied: documents still missing.
  const review = applications.filter((a) => a.status === "kyc_pending");
  const docs = applications.filter((a) => a.status === "applied");
  const active = partners.filter((p) => p.status === "active" || p.status === "paused");
  const suspended = partners.filter((p) => p.status === "suspended");
  const queue = tab === "review" ? review : tab === "docs" ? docs : [];
  const selected = queue.find((a) => a.id === picked) ?? queue[0];

  const tabs = [
    { value: "review", label: "Waiting for review", count: review.length },
    { value: "docs", label: "Missing documents", count: docs.length },
    { value: "active", label: "Active", count: active.length },
    { value: "suspended", label: "Suspended", count: suspended.length },
  ];

  const waiting = (/** @type {import("@/lib/partners.js").ApplicationReview} */ a) => {
    const days = waitingDays(a.submitted_at, now);
    return days === 0 ? "Today" : plural(days, "day", "days");
  };
  const applicantRows = queue.map((a) => {
    const t = trainingDone(a);
    return {
      id: a.id,
      who: {
        text: a.applicant.name,
        sub: a.society?.name ?? a.applicant.phone_e164,
        strong: true,
        href: `?tab=${tab}&applicant=${a.id}`,
      },
      role: KIND_LABEL[a.kind],
      docs: a.requirements.documents.some((d) => d.status === "missing" || d.status === "rejected")
        ? { badge: { tone: /** @type {const} */ ("danger"), label: "Missing" } }
        : { badge: { tone: /** @type {const} */ ("primary"), label: "Complete" } },
      training: `${t.done} of ${t.total}`,
      since: waiting(a),
    };
  });
  const partnerRows = (/** @type {import("@/lib/partners.js").PartnerStaff[]} */ list) =>
    list.map((p) => ({
      id: p.id,
      who: {
        text: p.name ?? p.display_name,
        sub: p.requested_society?.name ?? p.phone_e164 ?? undefined,
        strong: true,
        href: `/partners/${p.id}`,
      },
      role: KIND_LABEL[p.kind],
      docs: p.rating ? `${p.rating.average.toFixed(1)} of 5` : "Not rated yet",
      training:
        p.status === "paused"
          ? { badge: { tone: /** @type {const} */ ("outline"), label: "Paused" } }
          : p.status === "suspended"
            ? { badge: { tone: /** @type {const} */ ("danger"), label: "Suspended" } }
            : { badge: { tone: /** @type {const} */ ("primary"), label: "Active" } },
      since: formatDate(p.verified_at),
    }));
  const isQueue = tab === "review" || tab === "docs";

  /** Applicant links change the URL only; keep the page where it is instead of reloading it. @param {import("react").MouseEvent} e */
  const intercept = (e) => {
    const link = e.target instanceof Element ? e.target.closest('a[href^="?"]') : null;
    if (!link) return;
    e.preventDefault();
    setPicked(new URLSearchParams(link.getAttribute("href") ?? "").get("applicant"));
  };

  return (
    <>
      <div className="flex flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">Partners and KYC</h1>
        <p className="text-sm leading-5 text-fg-secondary">
          Only trained, verified people operate. Approving activates the role in the person’s app;
          rejecting needs a reason they’ll see.
        </p>
      </div>
      <Tabs
        label="Partner lists"
        tabs={tabs}
        value={tab}
        onChange={(v) => setTab(/** @type {typeof TABS[number]} */ (v))}
      >
        <div
          className={`grid grid-cols-[minmax(0,1fr)] items-start gap-4 ${isQueue ? "min-[900px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]" : ""}`}
        >
          {/* biome-ignore lint/a11y/noStaticElementInteractions: delegates clicks of the links inside the table */}
          <div className="min-w-0" onClick={intercept} onKeyDown={undefined}>
            <DataTable
              columns={[
                { key: "who", label: isQueue ? "Applicant" : "Partner", sortable: true },
                { key: "role", label: "Role" },
                { key: "docs", label: isQueue ? "Documents" : "Rating" },
                { key: "training", label: isQueue ? "Training" : "Status", align: "end" },
                { key: "since", label: isQueue ? "Waiting" : "Since" },
              ]}
              rows={
                tab === "active"
                  ? partnerRows(active)
                  : tab === "suspended"
                    ? partnerRows(suspended)
                    : applicantRows
              }
              caption={tabs.find((t) => t.value === tab)?.label}
              labels={{
                empty: {
                  review: "No applications are waiting for review.",
                  docs: "Nobody is missing documents.",
                  active: "No active partners yet.",
                  suspended: "No suspended partners.",
                }[tab],
              }}
            />
          </div>
          {isQueue && selected && <ReviewPanel key={selected.id} application={selected} />}
        </div>
      </Tabs>
      <Toasts />
    </>
  );
}

/** @param {{ application: import("@/lib/partners.js").ApplicationReview }} props */
function ReviewPanel({ application: a }) {
  const [reason, setReason] = useState("");
  const [grade, setGrade] = useState("");
  const [fields, setFields] = useState(/** @type {Record<string, string>} */ ({}));
  const [pending, start] = useTransition();
  // One Idempotency-Key per decision: retrying the same decision reuses it, a different one gets a new key.
  const attempt = useRef({ body: "", key: "" });
  const checks = reviewChecks(a);
  const name = a.applicant.name;
  // Approval needs every required document in (the API refuses anything but kyc_pending).
  const incomplete = a.status !== "kyc_pending";

  /** @param {"approve" | "reject"} outcome */
  function decide(outcome) {
    if (outcome === "reject" && !reason.trim()) {
      setFields({ reason: "Tell the applicant why" });
      return;
    }
    if (outcome === "approve" && a.kind === "kitchen" && !grade) {
      setFields({ hygiene_grade: "Pick the hygiene grade from the kitchen photos" });
      return;
    }
    start(async () => {
      const input = {
        outcome,
        ...(outcome === "reject" ? { reason: reason.trim() } : {}),
        ...(outcome === "approve" && a.kind === "kitchen" ? { hygiene_grade: grade } : {}),
      };
      const body = JSON.stringify(input);
      if (attempt.current.body !== body) attempt.current = { body, key: newKey() };
      const r = await decideApplication(a.id, attempt.current.key, input);
      if (!r.ok) {
        setFields(r.fields);
        if (r.message) toast(r.message);
      } else if (outcome === "reject") {
        toast(`${name} rejected. They will see your reason.`);
      } else {
        toast(
          r.status === "verified"
            ? `${name} approved. Their role is now active.`
            : `${name} approved. Their role opens once they finish training.`,
        );
      }
    });
  }

  /** Opens the files through five-minute links; the API logs each time. @param {string} documentId */
  async function view(documentId) {
    const r = await fileLinks(documentId);
    if (!r.ok) return toast(r.message);
    for (const url of r.urls) window.open(url, "_blank", "noopener");
  }

  return (
    <section
      aria-label="Review applicant"
      className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">{name}</h2>
        <Badge tone="accent">{KIND_LABEL[a.kind]}</Badge>
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        Applied from the customer app · shown to families as {a.display_name} · {scopeText(a)}.
      </p>
      <ul className="m-0 list-none p-0">
        {checks.map((c) => (
          <li
            key={c.key}
            className="flex items-baseline justify-between gap-3 border-b border-line py-2 text-sm"
          >
            <span>
              {c.label}
              {c.masked ? ` · ${c.masked}` : ""}
            </span>
            <span className="flex items-baseline gap-2 text-right">
              {c.documentId && (
                <button
                  type="button"
                  onClick={() => view(/** @type {string} */ (c.documentId))}
                  className="rounded-xs text-link underline outline-none focus-visible:shadow-focus"
                >
                  View
                </button>
              )}
              <Badge tone={CHECK_TONE[c.state]}>{c.state_text}</Badge>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-sm leading-5 text-fg-secondary">
        Documents sit in private storage; this page shows only masked numbers, and each time you
        open a file it is logged. No Aadhaar is stored.
      </p>
      {a.kind === "kitchen" && !incomplete && (
        <Select
          label="Hygiene grade, from the kitchen photos"
          placeholder="Pick a grade"
          options={GRADES}
          value={grade}
          onChange={(v) => {
            setGrade(v);
            setFields(({ hygiene_grade: _gone, ...rest }) => rest);
          }}
          error={fields.hygiene_grade}
        />
      )}
      <TextField
        label="Reason, if you reject"
        placeholder="Shown to the applicant"
        value={reason}
        onChange={(v) => {
          setReason(v);
          setFields(({ reason: _gone, ...rest }) => rest);
        }}
        isInvalid={!!fields.reason}
        errorMessage={fields.reason}
      />
      {incomplete && (
        <p className="text-sm leading-5 text-fg-secondary">
          A decision opens once every required document is in.
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="secondary"
          block
          isDisabled={pending || incomplete}
          onPress={() => decide("reject")}
        >
          Reject
        </Button>
        <Button
          variant="primary"
          block
          isDisabled={pending || incomplete}
          onPress={() => decide("approve")}
        >
          Approve and activate
        </Button>
      </div>
    </section>
  );
}
