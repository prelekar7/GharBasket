import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { request } from "@/lib/api.js";
import { enqueue } from "@/lib/outbox.js";
import { useSession } from "@/lib/session.js";

/** @typedef {"judi" | "g" | "kg" | "l" | "pc"} Unit */
/** @typedef {{ id: string, product_code: string, qty: number, addon: boolean, status: "todo" | "picked" | "short" }} PackLine */
/**
 * One bag to pack (hub_ops.packing_tasks). `route_seq` is the bag's place in the van; the society's
 * `stop` is the rider's stop.
 * @typedef {{ id: string, bag_qr: string, route_seq: number, society_id: string, flat: string, crate: string,
 *   kind: "box" | "tiffin", total_paise: number, status: "to_pack" | "packing" | "packed", done_at: string | null,
 *   lines: PackLine[] }} PackingTask
 */
/**
 * The morning at a hub (GET /v1/hubs/{id}/packing-tasks, M6; fields are our proposal). Products
 * are everything packed there today, so a scan of any of them can be named offline.
 * @typedef {{ hub: { id: string, name: string }, date: string, van_departs_at: string,
 *   societies: { id: string, name: string, stop: number, captain: string }[],
 *   products: Record<string, { name: string, unit: Unit, shelf: string }>, tasks: PackingTask[] }} PackingDay
 */
/**
 * A bag that takes a shortage and what that family chose.
 * @typedef {{ task_id: string, bag_qr: string, society: string, flat: string, qty: number,
 *   outcome?: "swap" | "refund", swap_name?: string, refund_paise?: number }} ShortBagOut
 */
/**
 * An item short today: the goods-receipt gap and the bags it can fall on, in that order (GET
 * /v1/hubs/{id}/shortages, our proposal to M6). `needed`/`received` are null for one reported at a bag.
 * @typedef {{ product_code: string, needed: number | null, received: number | null,
 *   bags: ShortBagOut[], reported: ShortBagOut[] }} ShortItem
 */

/** The packer's hub, from her role grant. */
export const useHubId = () =>
  useSession((s) => s.grants.find((g) => g.role === "packer")?.scope_id ?? null);

/** Today's bags in route order. Cached and persisted, so the queue works without signal once loaded. */
export function usePackingDay() {
  const hubId = useHubId();
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["packing", hubId, i18n.resolvedLanguage],
    enabled: !!hubId,
    queryFn: () => /** @type {Promise<PackingDay>} */ (request(`/v1/hubs/${hubId}/packing-tasks`)),
  });
}

/** Items short today and the bags they fall on. */
export function useShortages() {
  const hubId = useHubId();
  const { i18n } = useTranslation();
  return useQuery({
    queryKey: ["shortages", hubId, i18n.resolvedLanguage],
    enabled: !!hubId,
    queryFn: async () =>
      /** @type {{ items: ShortItem[] }} */ (await request(`/v1/hubs/${hubId}/shortages`)).items,
  });
}

/** Lines still to pick or mark short. @param {PackingTask} task */
export const openLines = (task) => task.lines.filter((l) => l.status === "todo");

/**
 * Packing writes (M6 §3). Each one changes the cached day at once and goes through the offline
 * outbox with its own Idempotency-Key, so it works without signal and is never applied twice.
 */
export function usePackingActions() {
  const client = useQueryClient();
  const hubId = useHubId();

  /** @param {string} taskId @param {(t: PackingTask) => PackingTask} change */
  const patch = (taskId, change) => {
    client.cancelQueries({ queryKey: ["packing", hubId] });
    client.setQueriesData(
      { queryKey: ["packing", hubId] },
      (/** @type {PackingDay | undefined} */ d) =>
        d && { ...d, tasks: d.tasks.map((t) => (t.id === taskId ? change(t) : t)) },
    );
  };
  /** First open line of a product becomes `status`. @param {PackingTask} t @param {string} code @param {PackLine["status"]} status */
  const setLine = (t, code, status) => {
    const line = t.lines.find((l) => l.product_code === code && l.status === "todo");
    return { ...t, lines: t.lines.map((l) => (l === line ? { ...l, status } : l)) };
  };
  /** @param {PackingTask} task @param {string} action @param {object} [body] */
  const send = (task, action, body) =>
    enqueue({ path: `/v1/packing-tasks/${task.id}/${action}`, body });

  return {
    /** Scanning a bag's label starts it. @param {PackingTask} task */
    start(task) {
      patch(task.id, (t) => ({ ...t, status: "packing" }));
      return send(task, "start", { bag_qr: task.bag_qr });
    },
    /** One scanned item. @param {PackingTask} task @param {string} code */
    pick(task, code) {
      patch(task.id, (t) => ({ ...setLine(t, code, "picked"), status: "packing" }));
      return send(task, "pick", { product_code: code });
    },
    /** @param {PackingTask} task @param {PackLine} line */
    markShort(task, line) {
      patch(task.id, (t) => setLine(t, line.product_code, "short"));
      client.invalidateQueries({ queryKey: ["shortages", hubId], refetchType: "none" });
      return send(task, "shortage", { product_code: line.product_code, qty: line.qty });
    },
    /** @param {PackingTask} task */
    close(task) {
      patch(task.id, (t) => ({ ...t, status: "packed", done_at: new Date().toISOString() }));
      return send(task, "complete");
    },
    /** A short item falls on these bags; each family's own choice decides what they get. @param {ShortItem} item @param {ShortBagOut[]} bags */
    reportShortage(item, bags) {
      const ids = new Set(bags.map((b) => b.task_id));
      client.cancelQueries({ queryKey: ["shortages", hubId] });
      client.setQueriesData(
        { queryKey: ["shortages", hubId] },
        (/** @type {ShortItem[] | undefined} */ d) =>
          d?.map((i) =>
            i.product_code === item.product_code
              ? {
                  ...i,
                  bags: i.bags.filter((b) => !ids.has(b.task_id)),
                  reported: [...i.reported, ...bags],
                }
              : i,
          ),
      );
      return Promise.all(
        bags.map((b) => {
          patch(b.task_id, (t) => setLine(t, item.product_code, "short"));
          return enqueue({
            path: `/v1/packing-tasks/${b.task_id}/shortage`,
            body: { product_code: item.product_code, qty: b.qty },
          });
        }),
      );
    },
  };
}
