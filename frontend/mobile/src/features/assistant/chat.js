import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { request } from "@/lib/api.js";

/**
 * @typedef {{ kind: "remove" | "add" | "qty", name: string, qty_label: string, delta_paise: number }
 *   | { kind: "meal", date: string, slot: "lunch" | "dinner", dish: string, delta_paise: number }} ProposalLine
 * @typedef {{ id: string, action: string, status: "pending" | "confirmed" | "rejected" | "expired", expires_at: string,
 *   box: { delivery_date: string, version: number }, lines: ProposalLine[], new_total_paise: number }} Proposal
 * @typedef {{ id: string, from: "me" | "bot", text: string, proposal?: Proposal | null, error?: string }} Message
 */

const KEY = "gb-assistant";
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

  const send = useMutation({
    mutationFn: (/** @type {string} */ text) =>
      /** @type {Promise<{ session_id: string, reply: { id: string, text: string }, proposal: Proposal | null }>} */ (
        request("/v1/assistant/messages", {
          method: "POST",
          body: {
            session_id: state.sessionId ?? undefined,
            client_message_id: crypto.randomUUID(),
            text,
          },
        })
      ),
    onMutate: (text) => add({ id: crypto.randomUUID(), from: "me", text }),
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

  return { messages: state.messages, send, confirm, reject };
}
