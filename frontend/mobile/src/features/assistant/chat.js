import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ASSISTANT_STORAGE_KEY } from "@/features/assistant/storage.js";
import { request } from "@/lib/api.js";

/**
 * The contract (backend/api/openapi.json, M13 §3). The card is the API's own sentence (`summary`, in
 * the person's language); `payload` carries the week it applies to and the price change in paise
 * (`price_change_paise`, edit_box_line only). The version the change is made against stays on the server.
 * @typedef {{ id: string, action: "edit_box_line" | "skip_week", status: "pending" | "confirmed" | "rejected" | "expired",
 *   expires_at: string, summary: string, payload: { delivery_date: string, price_change_paise?: number } & Record<string, unknown>,
 *   result: Record<string, unknown> | null }} Proposal
 * @typedef {{ session_id: string, reply: { id: string, role: "assistant", content: string }, proposals: Proposal[],
 *   source: "ml" | "fallback", tokens_today: number, tokens_cap: number }} Turn
 * @typedef {{ id: string, from: "me" | "bot", text: string, proposals?: Proposal[], error?: string }} Message
 */

const KEY = ASSISTANT_STORAGE_KEY;
/** A reply that takes longer than this is given up on, so the chat never hangs (ML never blocks a customer). */
const REPLY_TIMEOUT_MS = 25_000;

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

/** Errors where asking again can help: not the daily cap, which only tomorrow fixes. */
const NO_RETRY = new Set(["assistant.daily_cap"]);
/** Confirm failures where the proposal is stale and the person should ask for a fresh one. */
const STALE = new Set([
  "assistant.proposal_expired",
  "subscriptions.version_conflict",
  "assistant.proposal_decided",
  "assistant.no_cycle",
  "subscriptions.cutoff_passed",
  "subscriptions.not_editable",
]);
/** @param {unknown} e */
export const isStale = (e) =>
  STALE.has(/** @type {{ code?: string }} */ (e)?.code ?? "") ||
  [409, 412].includes(/** @type {{ status?: number }} */ (e)?.status ?? 0);
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
      return confirmProposal(p.id, keys.current[p.id]);
    },
    onSuccess: (r) => {
      setProposal(r.proposal);
      client.invalidateQueries({ queryKey: ["cycle"] });
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
    const i = state.messages.findIndex((m) => m.proposals?.some((x) => x.id === p.id));
    const question = state.messages.slice(0, i).findLast((m) => m.from === "me");
    setProposal({ ...p, status: "expired" });
    confirm.reset();
    if (question) ask(question.text);
  };

  return { messages: state.messages, send, ask, retry, askAgain, confirm, reject };
}
