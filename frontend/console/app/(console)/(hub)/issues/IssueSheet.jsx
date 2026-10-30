"use client";

import { Badge, Button, formatPaise, Sheet, TextField, toast } from "@gharbasket/ui";
import { useEffect, useRef, useState, useTransition } from "react";
import {
  ACTOR_LABEL,
  KIND_LABEL,
  OPEN_REASON,
  resolveBody,
  rupeesText,
  STATUS_LABEL,
} from "@/lib/order-issues.js";
import { dayLabel, dayTimeLabel } from "@/lib/when.js";
import { loadIssue, loadTimeline, resolveIssue } from "./actions.js";

/** How often the open timeline asks again, in milliseconds. */
const POLL_MS = 15_000;

/**
 * The issue sheet. It mounts the body only while an issue is picked, so every opening asks the API
 * again: the photo links last 5 minutes and each read of the contact is audited.
 * @param {{ issueId: string | null, onClose: () => void }} props
 */
export function IssueSheet({ issueId, onClose }) {
  return (
    <Sheet
      title="Issue"
      description="Check the photos, call the customer, then decide."
      open={issueId !== null}
      onOpenChange={(o) => !o && onClose()}
    >
      {issueId && <IssueBody key={issueId} id={issueId} onClose={onClose} />}
    </Sheet>
  );
}

/** @param {{ id: string, onClose: () => void }} props */
function IssueBody({ id, onClose }) {
  const [state, setState] = useState(
    /** @type {{ issue: any } | { error: string } | null} */ (null),
  );

  useEffect(() => {
    let live = true;
    loadIssue(id).then((r) => live && setState(r.ok ? { issue: r.issue } : { error: r.message }));
    return () => {
      live = false;
    };
  }, [id]);

  if (!state) return <p className="text-sm leading-5 text-fg-secondary">Loading the issue…</p>;
  if ("error" in state)
    return (
      <p role="alert" className="text-sm leading-5 font-semibold text-danger">
        {state.error}
      </p>
    );
  const { issue } = state;
  return (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">{KIND_LABEL[issue.kind] ?? issue.kind}</h2>
        <Badge tone={issue.status === "open" ? "accent" : "outline"}>
          {issue.status === "open"
            ? "Open"
            : issue.status === "credited"
              ? `Credited ${formatPaise(issue.credit_paise)}`
              : "Rejected"}
        </Badge>
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        Reported {dayTimeLabel(issue.created_at)} for the delivery on{" "}
        {dayLabel(issue.delivery_date)}
        {issue.open_reason ? `. ${OPEN_REASON[issue.open_reason] ?? issue.open_reason}` : ""}.
      </p>
      {issue.note && <p className="text-sm leading-5">“{issue.note}”</p>}
      <ul className="flex flex-col text-sm leading-5">
        {issue.items.map((/** @type {any} */ x) => (
          <li key={x.product_id} className="flex justify-between gap-3 border-b border-line py-1.5">
            <span>
              {x.qty} × {x.name}
            </span>
            <strong>{formatPaise(x.amount_paise)}</strong>
          </li>
        ))}
        <li className="flex justify-between gap-3 py-1.5 text-fg-secondary">
          <span>Order total {formatPaise(issue.order_total_paise)}</span>
          <span>Can still be credited {formatPaise(issue.creditable_paise)}</span>
        </li>
      </ul>
      {issue.auto_credited_7d_paise > 0 && (
        <p className="text-sm leading-5 text-fg-secondary">
          Credited automatically to this family in the last 7 days:{" "}
          {formatPaise(issue.auto_credited_7d_paise)}.
        </p>
      )}
      {issue.photos.length > 0 ? (
        <div className="grid grid-cols-3 gap-2">
          {issue.photos.map((/** @type {any} */ p, /** @type {number} */ n) => (
            <a key={p.url} href={p.url} target="_blank" rel="noreferrer">
              {/* biome-ignore lint/performance/noImgElement: a 5-minute signed link, not an optimisable asset */}
              <img
                src={p.url}
                alt={`Sent by the customer, ${n + 1} of ${issue.photos.length}`}
                className="aspect-square w-full rounded-sm object-cover"
              />
            </a>
          ))}
        </div>
      ) : (
        <p className="text-sm leading-5 text-fg-secondary">
          {issue.photo_count > 0
            ? "The photos can't be shown right now. Try again in a moment."
            : "No photos were added."}
        </p>
      )}
      {issue.contact ? (
        <p className="text-sm leading-5">
          Call back {issue.contact.name ?? "the customer"} on{" "}
          <a className="font-semibold underline" href={`tel:${issue.contact.phone_e164}`}>
            {issue.contact.phone_e164}
          </a>
          {issue.callback_due_at ? `, by ${dayTimeLabel(issue.callback_due_at)}` : ""}.
        </p>
      ) : (
        <p className="text-sm leading-5 text-fg-secondary">No phone number is on file.</p>
      )}
      {issue.status === "open" ? (
        <ResolveForm issue={issue} onDone={onClose} onSettled={setState} />
      ) : (
        issue.resolution && (
          <p className="text-sm leading-5 text-fg-secondary">
            Resolved {dayTimeLabel(issue.resolution.at)}
            {issue.resolution.by === "auto" ? " automatically" : " by the hub"}
            {issue.resolution.note ? `: “${issue.resolution.note}”` : ""}.
          </p>
        )
      )}
      <Timeline orderId={issue.order_id} />
    </>
  );
}

/**
 * Credit to the wallet or reject. One Idempotency-Key per form state: any change to what is typed
 * gives a new key, because the same key with another body is refused.
 * @param {{ issue: any, onDone: () => void, onSettled: (s: { issue: any }) => void }} props
 */
function ResolveForm({ issue, onDone, onSettled }) {
  const max = issue.max_credit_paise;
  const [outcome, setOutcome] = useState(/** @type {"credit" | "reject"} */ ("credit"));
  const [rupees, setRupees] = useState(rupeesText(max));
  const [note, setNote] = useState("");
  const [error, setError] = useState(/** @type {{ field: string, text: string } | null} */ (null));
  const [busy, run] = useTransition();
  const key = useRef("");

  /** @template T @param {(v: T) => void} set @returns {(v: T) => void} */
  const edit = (set) => (v) => {
    key.current = "";
    setError(null);
    set(v);
  };

  function submit() {
    const checked = resolveBody({ outcome, rupees, note, maxPaise: max });
    if ("error" in checked) return setError({ field: checked.field, text: checked.error });
    key.current ||= crypto.randomUUID();
    run(async () => {
      const r = await resolveIssue(issue.id, checked.body, key.current);
      if (r.ok) {
        toast(outcome === "credit" ? `Credited ${formatPaise(r.issue.credit_paise)}` : "Rejected");
        return onDone();
      }
      setError({ field: "form", text: r.message });
      if (r.settled) {
        const again = await loadIssue(issue.id);
        if (again.ok) onSettled({ issue: again.issue });
      }
    });
  }

  return (
    <form
      className="flex flex-col gap-3 rounded-md bg-bg p-3"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant={outcome === "credit" ? "primary" : "quiet"}
          onPress={() => edit(setOutcome)("credit")}
        >
          Credit the wallet
        </Button>
        <Button
          variant={outcome === "reject" ? "primary" : "quiet"}
          onPress={() => edit(setOutcome)("reject")}
        >
          Reject
        </Button>
      </div>
      {outcome === "credit" && (
        <TextField
          label="Amount to credit"
          prefix="₹"
          inputMode="decimal"
          value={rupees}
          onChange={edit(setRupees)}
          description={`Up to ${formatPaise(max)} now.`}
          isInvalid={error?.field === "rupees"}
          errorMessage={error?.field === "rupees" ? error.text : undefined}
          className="max-w-none"
        />
      )}
      <TextField
        label={outcome === "reject" ? "Why it is rejected" : "Note (optional)"}
        value={note}
        onChange={edit(setNote)}
        isInvalid={error?.field === "note"}
        errorMessage={error?.field === "note" ? error.text : undefined}
        className="max-w-none"
      />
      {error?.field === "form" && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {error.text}
        </p>
      )}
      <Button variant="primary" block isDisabled={busy} onPress={submit}>
        {outcome === "credit" ? "Credit now" : "Reject the issue"}
      </Button>
    </form>
  );
}

/**
 * The order's timeline, read only. It polls the JSON form of the events every 15 seconds while the
 * sheet is open.
 * @param {{ orderId: string }} props
 */
function Timeline({ orderId }) {
  const [rows, setRows] = useState(
    /** @type {{ id: string, status: string, actor: string, at: string }[] | null | false} */ (
      null
    ),
  );
  useEffect(() => {
    let live = true;
    const poll = () =>
      loadTimeline(orderId).then((r) => {
        if (live) setRows(r.ok ? r.items : (prev) => prev || false);
      });
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [orderId]);

  return (
    <section aria-label="Order timeline" className="flex flex-col gap-1">
      <h3 className="text-sm leading-5 font-bold">Order timeline</h3>
      {rows === null && <p className="text-sm leading-5 text-fg-secondary">Loading…</p>}
      {rows === false && (
        <p className="text-sm leading-5 text-fg-secondary">
          The timeline can't be shown right now.
        </p>
      )}
      {rows && (
        <ol className="flex flex-col text-sm leading-5">
          {rows.map((e) => (
            <li key={e.id} className="flex justify-between gap-3 border-b border-line py-1.5">
              <span>
                <strong>{STATUS_LABEL[e.status] ?? e.status}</strong>
                <span className="text-fg-secondary"> · {ACTOR_LABEL[e.actor] ?? e.actor}</span>
              </span>
              <span className="whitespace-nowrap text-fg-secondary">{dayTimeLabel(e.at)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
