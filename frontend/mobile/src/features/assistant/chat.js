import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ASSISTANT_STORAGE_KEY } from "@/features/assistant/storage.js";
import { request } from "@/lib/api.js";

/**
 * @typedef {{ kind: "remove" | "add" | "qty", name: string, qty_label: string, delta_paise: number }
 *   | { kind: "meal", date: string, slot: "lunch" | "dinner", dish: string, delta_paise: number }} ProposalLine
 * @typedef {{ id: string, action: string, status: "pending" | "confirmed" | "rejected" | "expired", expires_at: string,
 *   box: { delivery_date: string, version: number }, lines: ProposalLine[], new_total_paise: number }} Proposal
 * @typedef {{ id: string, from: "me" | "bot", text: string, proposal?: Proposal | null, error?: string }} Message
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

/** Errors where asking again can help: not the daily limit, which only tomorrow fixes. */
const NO_RETRY = new Set(["assistant.daily_limit"]);
/** Confirm failures where the proposal is stale and the person should ask for a fresh one. */
const STALE = new Set([
  "assistant.proposal_expired",
  "subscriptions.version_conflict",
  "orders.cutoff_passed",
]);
/** @param {unknown} e */
export const isStale = (e) =>
  STALE.has(/** @type {{ code?: string }} */ (e)?.code ?? "") ||
  [409, 412].includes(/** @type {{ status?: number }} */ (e)?.status ?? 0);
/** @param {string | undefined} code */
export const canRetry = (code) => !NO_RETRY.has(code ?? "");
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
      messages: s.messages.map((m) => (m.proposal?.id === p.id ? { ...m, proposal: p } : m)),
    }));

  // The message id is the client_message_id, so a retry of a timed-out message is not a second one.
  const send = useMutation({
    mutationFn: (/** @type {{ text: string, id: string, retried?: boolean }} */ m) =>
      withTimeout(
        (signal) =>
          /** @type {Promise<{ session_id: string, reply: { id: string, text: string }, proposal: Proposal | null }>} */ (
            request("/v1/assistant/messages", {
              method: "POST",
              body: {
                session_id: state.sessionId ?? undefined,
                client_message_id: m.id,
                text: m.text,
              },
              signal,
            })
          ),
      ),
    onMutate: ({ text, id, retried }) => {
      if (retried)
        setState((s) => ({
          ...s,
          messages: s.messages.filter((m, i) => !(m.error && i === s.messages.length - 1)),
        }));
      else add({ id, from: "me", text });
    },
    onSuccess: (r) => {
      setState((s) => ({ ...s, sessionId: r.session_id }));
      add({ id: r.reply.id, from: "bot", text: r.reply.text, proposal: r.proposal });
    },
    onError: (e) =>
      add({
        id: crypto.randomUUID(),
        from: "bot",
        text: "",
        error: /** @type {{ code?: string }} */ (e).code ?? "http.error",
      }),
  });

  const confirm = useMutation({
    mutationFn: (/** @type {Proposal} */ p) => {
      keys.current[p.id] ??= crypto.randomUUID();
      return /** @type {Promise<{ proposal: Proposal, box: { delivery_date: string, version: number, total_paise: number } }>} */ (
        request(`/v1/assistant/confirm/${p.id}`, {
          method: "POST",
          body: {},
          headers: { "idempotency-key": keys.current[p.id], "if-match": String(p.box.version) },
        })
      );
    },
    onSuccess: (r) => {
      setProposal(r.proposal);
      client.invalidateQueries({ queryKey: ["cycle"] });
      client.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  const reject = useMutation({
    mutationFn: (/** @type {Proposal} */ p) =>
      /** @type {Promise<Proposal>} */ (
        request(`/v1/assistant/proposals/${p.id}/reject`, { method: "POST", body: {} })
      ),
    onSuccess: setProposal,
  });

  /** Ask something new. @param {string} text */
  const ask = (text) => send.mutate({ text, id: crypto.randomUUID() });
  /** Send the last message again after a failure, with the same client_message_id. */
  const retry = () => {
    const last = state.messages.findLast((m) => m.from === "me");
    if (last) send.mutate({ text: last.text, id: last.id, retried: true });
  };
  /** A stale proposal: mark it expired and ask the same question again for a fresh one. @param {Proposal} p */
  const askAgain = (p) => {
    const i = state.messages.findIndex((m) => m.proposal?.id === p.id);
    const question = state.messages.slice(0, i).findLast((m) => m.from === "me");
    setProposal({ ...p, status: "expired" });
    confirm.reset();
    if (question) ask(question.text);
  };

  return { messages: state.messages, send, ask, retry, askAgain, confirm, reject };
}
