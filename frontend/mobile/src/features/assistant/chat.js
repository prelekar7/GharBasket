import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ASSISTANT_STORAGE_KEY } from "@/features/assistant/storage.js";
import { request } from "@/lib/api.js";

/**
 * The contract (backend/api/openapi.json, M13 §3). The card is the API's own sentence (`summary`, in
 * the person's language). For edit_box_line and skip_week, `payload` carries the week it applies to
 * (`delivery_date`) and the price change in paise (`price_change_paise`, edit_box_line only); the
 * version the change is made against stays on the server. For book_tiffin (assistant
 * tiffin_cards.py), it carries the day and meal, the portions and the total the API priced, and a
 * confirmed card's `result` names the order M4 booked.
 * @typedef {{ delivery_date?: string, price_change_paise?: number, menu_id?: string, date?: string,
 *   meal?: "lunch" | "dinner", portions?: number, unit_price_paise?: number, total_paise?: number,
 *   dishes?: string[] } & Record<string, unknown>} ProposalPayload
 * @typedef {{ order_id: string, status: string, delivery_date: string, meal: "lunch" | "dinner",
 *   portions: number, total_paise: number }} TiffinBooked A confirmed book_tiffin card's `result`.
 * @typedef {{ id: string, action: "edit_box_line" | "skip_week" | "book_tiffin", status: "pending" | "confirmed" | "rejected" | "expired",
 *   expires_at: string, summary: string, payload: ProposalPayload,
 *   result: Record<string, unknown> | null }} Proposal
 * @typedef {{ session_id: string, reply: { id: string, role: "assistant", content: string }, proposals: Proposal[],
 *   source: "ml" | "fallback", tokens_today: number, tokens_cap: number }} Turn
 * @typedef {{ id: string, from: "me" | "bot", text: string, proposals?: Proposal[], error?: string }} Message
 */

const KEY = ASSISTANT_STORAGE_KEY;
/**
 * A reply that takes longer than this is given up on, so the chat never hangs (ML never blocks a
 * customer). The API waits up to 30 s for ML (`assistant_timeout_s`) and then answers itself with
 * `source: "fallback"`, so the app waits longer than that, or the fallback would never be seen and a
 * retry would spend a second turn.
 */
const REPLY_TIMEOUT_MS = 40_000;

/** The longest message the API takes (`MessageIn.text`, max_length 2000). */
export const MAX_MESSAGE = 2000;

/**
 * Run a request with a deadline: abort it and fail with `assistant.timeout` when it runs long.
 * @template T
 * @param {(signal: AbortSignal) => Promise<T>} run
 * @returns {Promise<T>}
 */
function withTimeout(run) {
  const controller = new AbortController();
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let timer;
  const deadline = /** @type {Promise<never>} */ (
    new Promise((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(Object.assign(new Error("Ghar Saathi timed out"), { code: "assistant.timeout" }));
      }, REPLY_TIMEOUT_MS);
    })
  );
  return Promise.race([run(controller.signal), deadline]).finally(() => clearTimeout(timer));
}

/** Errors where asking again cannot help: the daily cap (only tomorrow fixes it), or a message the API refused (422). */
const NO_RETRY = new Set(["assistant.daily_cap", "request.invalid"]);
/** Confirm failures where the proposal is stale and the person should ask for a fresh one. */
const STALE = new Set([
  "assistant.proposal_expired",
  "subscriptions.version_conflict",
  "assistant.no_cycle",
  "subscriptions.cutoff_passed",
  "subscriptions.not_editable",
  // book_tiffin: the total moved since the card, or the menu closed (21:00 the day before) or was
  // taken off sale. A fresh card shows today's price or another menu.
  "assistant.price_changed",
  "orders.menu_unknown",
]);
/**
 * Confirm failures that asking again cannot fix, though most are a 409: the first confirm is still
 * running; the card was already decided (confirmOrRead resolves it to the card's real status, and
 * asking again could repeat a change); the meal is already booked (the problem names the order);
 * the kitchen's slot is full; this hub has no tiffin slot for the meal.
 */
const FINAL = new Set([
  "request.idempotency_in_progress",
  "assistant.proposal_decided",
  "orders.tiffin_already_booked",
  "orders.slot_full",
  "orders.slot_invalid",
]);
/** @param {unknown} e */
export const isStale = (e) => {
  const code = /** @type {{ code?: string }} */ (e)?.code ?? "";
  if (FINAL.has(code)) return false;
  return (
    STALE.has(code) || [409, 412].includes(/** @type {{ status?: number }} */ (e)?.status ?? 0)
  );
};

/**
 * The order a book_tiffin confirm found already booked for that date and meal (409
 * `orders.tiffin_already_booked` carries `order_id`), to link to it; null for any other failure.
 * @param {unknown} e @returns {string | null}
 */
export function bookedOrderId(e) {
  const err = /** @type {{ code?: string, extra?: Record<string, unknown> }} */ (e);
  if (err?.code !== "orders.tiffin_already_booked") return null;
  const id = err.extra?.order_id;
  return typeof id === "string" ? id : null;
}

/** The day a card is about: a tiffin's date, else the week's box date. @param {Proposal} p @returns {string | undefined} */
export const cardDate = (p) =>
  p.action === "book_tiffin" ? p.payload.date : p.payload.delivery_date;

/** A confirmed book_tiffin card's booked order, or null. @param {Proposal} p @returns {TiffinBooked | null} */
export function tiffinBooked(p) {
  if (p.action !== "book_tiffin" || p.status !== "confirmed" || !p.result) return null;
  return typeof p.result.order_id === "string" ? /** @type {TiffinBooked} */ (p.result) : null;
}
/** @param {string | undefined} code */
export const canRetry = (code) => !NO_RETRY.has(code ?? "");
/**
 * POST /v1/assistant/messages. MessageIn is `{ session_id?, text }` and nothing more (extra fields are
 * a 422), so there is no client message id: a retry simply sends the same text again.
 * @param {string | null} sessionId @param {string} text @param {AbortSignal} [signal]
 * @returns {Promise<Turn>}
 */
export const postMessage = (sessionId, text, signal) =>
  request("/v1/assistant/messages", {
    method: "POST",
    body: { session_id: sessionId ?? undefined, text },
    signal,
  });

/**
 * POST /v1/assistant/confirm/{id}: no body and no If-Match, the API applies the change at the week
 * version stored with the card (412 `subscriptions.version_conflict` if the week moved on).
 * @param {string} id @param {string} idempotencyKey
 * @returns {Promise<{ proposal: Proposal }>}
 */
export const confirmProposal = (id, idempotencyKey) =>
  request(`/v1/assistant/confirm/${id}`, {
    method: "POST",
    headers: { "idempotency-key": idempotencyKey },
  });

/**
 * GET /v1/assistant/sessions/{id}: the conversation's messages and its cards with their real status.
 * @param {string} id @returns {Promise<{ id: string, proposals: Proposal[] }>}
 */
export const getSession = (id) => request(`/v1/assistant/sessions/${id}`);

/**
 * Confirm a card. If the API says it was already decided (409 `assistant.proposal_decided`: confirmed
 * in another tab or before a reload, or expired by the hourly job), read the session and answer with
 * the card as it really is, so the screen shows "done" or "expired" instead of an error.
 * @param {string | null} sessionId @param {Proposal} p @param {string} idempotencyKey
 * @returns {Promise<{ proposal: Proposal }>}
 */
export async function confirmOrRead(sessionId, p, idempotencyKey) {
  try {
    return await confirmProposal(p.id, idempotencyKey);
  } catch (e) {
    if (/** @type {{ code?: string }} */ (e)?.code !== "assistant.proposal_decided" || !sessionId)
      throw e;
    const real = (await getSession(sessionId).catch(() => null))?.proposals?.find(
      (x) => x.id === p.id,
    );
    if (!real) throw e;
    return { proposal: real };
  }
}

/** POST /v1/assistant/proposals/{id}/reject: nothing changes. @param {string} id @returns {Promise<Proposal>} */
export const rejectProposal = (id) =>
  request(`/v1/assistant/proposals/${id}/reject`, { method: "POST" });

/** @returns {{ sessionId: string | null, messages: Message[] }} */
const restore = () => {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "null") ?? { sessionId: null, messages: [] };
  } catch {
    return { sessionId: null, messages: [] };
  }
};

/**
 * One Ghar Saathi conversation (M13): send a message, then confirm or turn down any change it
 * proposes. Nothing changes until the person says yes (W12). Kept for the browser tab only.
 */
export function useAssistant() {
  const client = useQueryClient();
  const [state, setState] = useState(restore);
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  useEffect(() => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      // Private mode: the chat just isn't kept.
    }
  }, [state]);

  const add = (/** @type {Message} */ m) =>
    setState((s) => ({ ...s, messages: [...s.messages, m] }));
  const setProposal = (/** @type {Proposal} */ p) =>
    setState((s) => ({
      ...s,
      messages: s.messages.map((m) =>
        m.proposals?.some((x) => x.id === p.id)
          ? { ...m, proposals: m.proposals.map((x) => (x.id === p.id ? p : x)) }
          : m,
      ),
    }));

  const send = useMutation({
    mutationFn: (/** @type {{ text: string, retried?: boolean }} */ m) =>
      withTimeout((signal) => postMessage(state.sessionId, m.text, signal)),
    onMutate: ({ text, retried }) => {
      if (retried)
        setState((s) => ({
          ...s,
          messages: s.messages.filter((m, i) => !(m.error && i === s.messages.length - 1)),
        }));
      else add({ id: crypto.randomUUID(), from: "me", text });
    },
    onSuccess: (r) => {
      setState((s) => ({ ...s, sessionId: r.session_id }));
      add({ id: r.reply.id, from: "bot", text: r.reply.content, proposals: r.proposals });
    },
    onError: (e) => {
      const code = /** @type {{ code?: string }} */ (e).code ?? "http.error";
      // A session the API no longer knows (404): drop it, so the retry starts a fresh conversation.
      if (code === "assistant.not_found") setState((s) => ({ ...s, sessionId: null }));
      add({ id: crypto.randomUUID(), from: "bot", text: "", error: code });
    },
  });

  const confirm = useMutation({
    mutationFn: (/** @type {Proposal} */ p) => {
      keys.current[p.id] ??= crypto.randomUUID();
      return confirmOrRead(state.sessionId, p, keys.current[p.id]);
    },
    onSuccess: (r) => {
      setProposal(r.proposal);
      client.invalidateQueries({ queryKey: ["cycle"] });
      // The Week tab (the cycle) and Orders both show a booked tiffin, and the box a changed week.
      client.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  const reject = useMutation({
    mutationFn: (/** @type {Proposal} */ p) => rejectProposal(p.id),
    onSuccess: setProposal,
  });

  /** Ask something new. @param {string} text */
  const ask = (text) => send.mutate({ text });
  /** Send the last message again after a failure. */
  const retry = () => {
    const last = state.messages.findLast((m) => m.from === "me");
    if (last) send.mutate({ text: last.text, retried: true });
  };
  /** A stale proposal: mark it expired and ask the same question again for a fresh one. @param {Proposal} p */
  const askAgain = (p) => {
    if (send.isPending) return; // one question at a time, as the message box allows
    const i = state.messages.findIndex((m) => m.proposals?.some((x) => x.id === p.id));
    const question = state.messages.slice(0, i).findLast((m) => m.from === "me");
    setProposal({ ...p, status: "expired" });
    confirm.reset();
    if (question) ask(question.text);
  };

  return { messages: state.messages, send, ask, retry, askAgain, confirm, reject };
}
