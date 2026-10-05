"use client";

import { Badge, Button, Tabs, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { formatDate, newKey, plural } from "@/lib/fleet.js";
import { Toasts } from "../_fleet/Toasts.jsx";
import { decideApplication } from "./actions.js";

const TABS = ["review", "docs", "active", "suspended"];
const CHECK_TONE = /** @type {const} */ ({ ok: "primary", look: "accent", missing: "danger" });

/**
 * Partners and KYC: the review queue with a decision panel, and the hub's active and suspended partners.
 * @param {{ applications: import("./data.js").Application[], partners: import("./data.js").PartnerRow[] }} props
 */
export function PartnersView({ applications, partners }) {
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("review").withOptions({ clearOnDefault: true }),
  );
  const [picked, setPicked] = useQueryState("applicant", parseAsString);

  const review = applications.filter((a) => a.status === "in_review");
  const docs = applications.filter((a) => a.status === "kyc_pending");
  const active = partners.filter((p) => p.status === "active");
  const suspended = partners.filter((p) => p.status === "suspended");
  const queue = tab === "review" ? review : tab === "docs" ? docs : [];
  const selected = queue.find((a) => a.id === picked) ?? queue[0];

  const tabs = [
    { value: "review", label: "Waiting for review", count: review.length },
    { value: "docs", label: "Missing documents", count: docs.length },
    { value: "active", label: "Active", count: active.length },
    { value: "suspended", label: "Suspended", count: suspended.length },
  ];

  const waiting = (/** @type {import("./data.js").Application} */ a) =>
    a.waiting_days === 0 ? "Today" : plural(a.waiting_days, "day", "days");
  const applicantRows = queue.map((a) => ({
    id: a.id,
    who: { text: a.name, sub: a.society, strong: true, href: `?tab=${tab}&applicant=${a.id}` },
    role: a.kind_label,
    docs: a.checks.some((c) => c.state === "missing" && c.kind !== "training")
      ? { badge: { tone: /** @type {const} */ ("danger"), label: "Missing" } }
      : { badge: { tone: /** @type {const} */ ("primary"), label: "Complete" } },
    training: `${a.training.done} of ${a.training.total}`,
    since: waiting(a),
  }));
  const partnerRows = (/** @type {import("./data.js").PartnerRow[]} */ list) =>
    list.map((p) => ({
      id: p.id,
      who: { text: p.name, sub: p.society, strong: true, href: `/partners/${p.id}` },
      role: p.kind_label,
      docs: p.rating ? `${p.rating.toFixed(1)} of 5` : "Not rated yet",
      training: `${p.training.done} of ${p.training.total}`,
      since: formatDate(p.since),
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
                { key: "training", label: "Training", align: "end" },
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

/** @param {{ application: import("./data.js").Application }} props */
function ReviewPanel({ application: a }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  // One Idempotency-Key per decision: retrying the same decision reuses it, a different one gets a new key.
  const attempt = useRef({ body: "", key: "" });
  const incomplete = a.status !== "in_review" || a.checks.some((c) => c.state === "missing");

  /** @param {"approve" | "reject"} decision */
  function decide(decision) {
    if (decision === "reject" && !reason.trim()) {
      setError("Tell the applicant why");
      return;
    }
    start(async () => {
      const input = { decision, ...(decision === "reject" ? { reason: reason.trim() } : {}) };
      const body = JSON.stringify(input);
      if (attempt.current.body !== body) attempt.current = { body, key: newKey() };
      const r = await decideApplication(a.id, attempt.current.key, input);
      if (r.ok) {
        toast(
          decision === "approve"
            ? `${a.name} approved. Their role is now active.`
            : `${a.name} rejected. They will see your reason.`,
        );
      } else if (r.reason) {
        setError(r.reason);
      } else {
        toast(r.message);
      }
    });
  }

  return (
    <section
      aria-label="Review applicant"
      className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">{a.name}</h2>
        <Badge tone="accent">{a.kind_label}</Badge>
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        Applied from the customer app · lives in {a.society} · {a.scope}.
      </p>
      <ul className="m-0 list-none p-0">
        {a.checks.map((c) => (
          <li
            key={c.kind}
            className="flex items-baseline justify-between gap-3 border-b border-line py-2 text-sm"
          >
            <span>
              {c.label}
              {c.masked ? ` · ${c.masked}` : ""}
            </span>
            <span className="text-right">
              <Badge tone={CHECK_TONE[c.state]}>{c.state_text}</Badge>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-sm leading-5 text-fg-secondary">
        Documents sit in private storage; this page shows only masked numbers. No Aadhaar is stored.
      </p>
      <TextField
        label="Reason, if you reject"
        placeholder="Shown to the applicant"
        value={reason}
        onChange={(v) => {
          setReason(v);
          setError("");
        }}
        isInvalid={!!error}
        errorMessage={error}
      />
      {incomplete && (
        <p className="text-sm leading-5 text-fg-secondary">
          Approval opens once every document and training module is in.
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" block isDisabled={pending} onPress={() => decide("reject")}>
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
