import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * @typedef {{ action: string, count: number }} LostAction
 * @typedef {object} LostActions
 * @property {LostAction[]} items Actions still in the outbox when the session ended; empty = no notice.
 * @property {(items: LostAction[]) => void} show
 * @property {() => void} dismiss
 */

/**
 * The notice for actions that were never sent because the session ended (lib/outbox.js
 * summarizeOutbox). The outbox itself is wiped with the rest of the person's data, so this is
 * the only trace: it stays, through a restart and a new sign-in, until the person dismisses it.
 * It holds action names and counts only, never what they were about.
 */
export const useLostActions = create(
  /** @type {import("zustand").StateCreator<LostActions, [], [["zustand/persist", unknown]]>} */ (
    persist(
      (set) => ({
        items: [],
        show: (items) => set({ items }),
        dismiss: () => set({ items: [] }),
      }),
      { name: "gb-lost-actions", partialize: (s) => ({ items: s.items }) },
    )
  ),
);
