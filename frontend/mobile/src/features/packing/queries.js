import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { todayIst } from "@/features/subscriptions/format.js";
import { request } from "@/lib/api.js";
import { db, enqueue, flush } from "@/lib/outbox.js";
import { useSession } from "@/lib/session.js";
import { isDone } from "./lines.js";

/** @typedef {{ qty: number, unit: string, label: string }} Step */
/** @typedef {{ product_id: string, slug: string, name: string, step: Step }} PackProduct */
/**
 * One line of a bag, in steps (PackingLineOut). `substitute` is set once the line is swapped; the
 * line is done when `picked_qty + short_qty = qty`.
 * @typedef {PackProduct & { qty: number, picked_qty: number, short_qty: number,
 *   substitute: PackProduct | null, done: boolean }} PackLine
 */
/**
 * One bag to pack (PackingTaskOut, GET /v1/hubs/{id}/packing-tasks). `seq` is its place in the
 * queue; `route` is set once M7 publishes routes; `crate_no` is set when the bag is closed.
 * @typedef {{ id: string, hub_id: string, order_id: string, order_kind: "box" | "addon", delivery_date: string,
 *   slot: { id: string, label: string, start: string, end: string } | null, society: { id: string, name: string },
 *   flat_label: string | null, seq: number, route: { route_id: string, stop_seq: number } | null,
 *   status: "queued" | "picking" | "packed", packer: { user_id: string, name: string | null } | null,
 *   bag_qr: string, bags: number, crate_no: number | null, lines_total: number, lines_done: number,
 *   started_at: string | null, packed_at: string | null, version: number }} PackingTask
 */
/** One bag with its lines (PackingTaskDetail, GET /v1/packing-tasks/{id}). @typedef {PackingTask & { lines: PackLine[] }} PackingDetail */
/**
 * The morning at a hub: the hub (name, and the van's time from its day timings) and every task.
 * @typedef {{ hub: { id: string, name: string }, date: string, van_departs_at: string, tasks: PackingTask[] }} PackingDay
 */
/**
 * A bag that takes a shortage and what that family gets: a swap they allowed (`substitute`) or money back.
 * @typedef {{ task_id: string, bag_qr: string, society: string, flat_label: string | null, qty: number,
 *   outcome: "swap" | "refund", substitute: PackProduct | null, refund_paise: number }} ShortBagOut
 */
/**
 * An item short today: the goods-receipt gap in steps and the bags it can fall on, then the bags it
 * has (GET /v1/hubs/{id}/shortages, a mock-only proposal to M6). `needed`/`received` are null for
 * one reported at a bag.
 * @typedef {PackProduct & { needed: number | null, received: number | null,
 *   bags: ShortBagOut[], reported: ShortBagOut[] }} ShortItem
 */

/** The packer's hub, from her role grant. */
export const useHubId = () =>
  useSession((s) => s.grants.find((g) => g.role === "packer")?.scope_id ?? null);

/** Every task of the day by seq, following the cursor. @param {string} hubId */
async function allTasks(hubId) {
  /** @type {PackingTask[]} */
  const tasks = [];
  let cursor = "";
  do {
    const page = /** @type {{ items: PackingTask[], next_cursor: string | null }} */ (
      await request(`/v1/hubs/${hubId}/packing-tasks?limit=200${cursor}`)
    );
    tasks.push(...page.items);
    cursor = page.next_cursor ? `&cursor=${encodeURIComponent(page.next_cursor)}` : "";
  } while (cursor);
  return tasks;
}

/** Today's bags in queue order. Cached and persisted, so the queue works without signal once loaded. */
export function usePackingDay() {
  const hubId = useHubId();
  return useQuery({
    queryKey: ["packing", hubId, "day"],
    enabled: !!hubId,
    queryFn: async () => {
      const [hub, tasks] = await Promise.all([
        /** @type {Promise<{ id: string, name: string, timings: { dispatch: string } }>} */ (
          request(`/v1/hubs/${hubId}`)
        ),
        allTasks(/** @type {string} */ (hubId)),
      ]);
      return /** @type {PackingDay} */ ({
        hub: { id: hub.id, name: hub.name },
        date: tasks[0]?.delivery_date ?? todayIst(),
        van_departs_at: hub.timings.dispatch,
        tasks,
      });
    },
  });
}

/** @param {string | null} hubId @param {string} taskId @param {string} lang */
const taskQuery = (hubId, taskId, lang) => ({
  queryKey: ["packing", hubId, "task", taskId, lang],
  queryFn: () => /** @type {Promise<PackingDetail>} */ (request(`/v1/packing-tasks/${taskId}`)),
});

/** One bag with its lines, in the person's language. @param {string | undefined} taskId */
export function usePackingTask(taskId) {
  const hubId = useHubId();
  const { i18n } = useTranslation();
  return useQuery({
    ...taskQuery(hubId, /** @type {string} */ (taskId), i18n.resolvedLanguage ?? "en-IN"),
    enabled: !!hubId && !!taskId,
  });
}

/** Loads the next few bags' lines while there is signal, so picking them works without it. @param {PackingTask[]} tasks */
export function usePrefetchNext(tasks) {
  const client = useQueryClient();
  const hubId = useHubId();
  const { i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const ids = tasks
    .filter((t) => t.status !== "packed")
    .slice(0, 3)
    .map((t) => t.id);
  const wanted = ids.join();
  useEffect(() => {
    for (const id of wanted.split(",").filter(Boolean))
      client.prefetchQuery(taskQuery(hubId, id, lang));
  }, [client, hubId, lang, wanted]);
}

/** Items short today and the bags they fall on (mock-only proposal, see ShortItem). */
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

/**
 * Packing writes (M6 §3.2). Each one changes the cached queue and bag at once and goes through
 * the offline outbox with its own Idempotency-Key and `client_at` (when the packer tapped), so it
 * works without signal and is never applied twice. Pick, shortage and complete need a started
 * bag, so a bag opened from the list is started first.
 */
export function usePackingActions() {
  const client = useQueryClient();
  const hubId = useHubId();
  const dayKey = ["packing", hubId, "day"];
  const taskKey = (/** @type {string} */ id) => ["packing", hubId, "task", id];

  /** @param {string} id @param {(t: PackingTask) => PackingTask} change */
  const patchRow = (id, change) =>
    client.setQueriesData(
      { queryKey: dayKey },
      (/** @type {PackingDay | undefined} */ d) =>
        d && { ...d, tasks: d.tasks.map((t) => (t.id === id ? change(t) : t)) },
    );
  /** @param {string} id @param {(t: PackingDetail) => PackingDetail} change */
  const patchDetail = (id, change) =>
    client.setQueriesData(
      { queryKey: taskKey(id) },
      (/** @type {PackingDetail | undefined} */ d) => d && change(d),
    );
  /** @param {PackingTask} task @param {(t: PackingTask) => PackingTask} change */
  const patchTask = (task, change) => {
    client.cancelQueries({ queryKey: ["packing", hubId] });
    patchRow(task.id, change);
    patchDetail(task.id, /** @type {any} */ (change));
  };
  /** One line of the open bag, and the done count the queue shows. @param {PackingTask} task @param {string} productId @param {(l: PackLine) => PackLine} change */
  const patchLine = (task, productId, change) => {
    client.cancelQueries({ queryKey: ["packing", hubId] });
    /** @type {number | null} */
    let done = null;
    patchDetail(task.id, (d) => {
      const lines = d.lines.map((l) => (l.product_id === productId ? change(l) : l));
      done = lines.filter(isDone).length;
      return { ...d, lines, lines_done: done };
    });
    if (done !== null)
      patchRow(task.id, (t) => ({ ...t, lines_done: /** @type {number} */ (done) }));
  };
  /** @param {PackingTask} task @param {string} action @param {object} body */
  const send = (task, action, body) =>
    enqueue({
      path: `/v1/packing-tasks/${task.id}/${action}`,
      body: { ...body, client_at: new Date().toISOString() },
    });

  const actions = {
    /** Scanning a bag's label starts it. @param {PackingTask} task */
    start(task) {
      patchTask(task, (t) => ({ ...t, status: "picking" }));
      return send(task, "start", { bag_qr: task.bag_qr });
    },
    /** @param {PackingTask} task */
    started(task) {
      const open = /** @type {PackingTask | undefined} */ (
        client.getQueriesData({ queryKey: taskKey(task.id) })[0]?.[1]
      );
      if ((open ?? task).status === "queued") actions.start(task);
    },
    /** One scanned item: every step still to pick goes in the bag. @param {PackingTask} task @param {PackLine} line */
    pick(task, line) {
      actions.started(task);
      const picked = line.qty - line.short_qty;
      patchLine(task, line.product_id, (l) => ({ ...l, picked_qty: picked }));
      return send(task, "pick", { product_id: line.product_id, picked_qty: picked });
    },
    /** The steps not picked are refunded. @param {PackingTask} task @param {PackLine} line */
    markShort(task, line) {
      actions.started(task);
      const short = line.qty - line.picked_qty;
      patchLine(task, line.product_id, (l) => ({ ...l, short_qty: short }));
      client.invalidateQueries({ queryKey: ["shortages", hubId], refetchType: "none" });
      return send(task, "shortage", {
        product_id: line.product_id,
        short_qty: short,
        substitute_product_id: null,
      });
    },
    /** Closes the bag. The crate number is the server's: it arrives once the outbox has sent this. @param {PackingTask} task */
    async close(task) {
      actions.started(task);
      patchTask(task, (t) => ({ ...t, status: "packed", packed_at: new Date().toISOString() }));
      await send(task, "complete", {});
      await flush();
      const waiting = await db.table("outbox").where("status").equals("waiting").count();
      if (waiting === 0) client.invalidateQueries({ queryKey: ["packing", hubId] });
    },
    /**
     * A short item falls on these bags; each family's own choice decides a swap or money back.
     * @param {ShortItem} item @param {ShortBagOut[]} bags
     */
    reportShortage(item, bags) {
      const ids = new Set(bags.map((b) => b.task_id));
      client.cancelQueries({ queryKey: ["shortages", hubId] });
      client.setQueriesData(
        { queryKey: ["shortages", hubId] },
        (/** @type {ShortItem[] | undefined} */ d) =>
          d?.map((i) =>
            i.product_id === item.product_id
              ? {
                  ...i,
                  bags: i.bags.filter((b) => !ids.has(b.task_id)),
                  reported: [...i.reported, ...bags],
                }
              : i,
          ),
      );
      const day = client.getQueryData(dayKey);
      return Promise.all(
        bags.map((b) => {
          const row = /** @type {PackingDay | undefined} */ (day)?.tasks.find(
            (t) => t.id === b.task_id,
          );
          if (row) actions.started(row);
          patchRow(b.task_id, (t) => ({ ...t, lines_done: t.lines_done + 1 }));
          client.invalidateQueries({ queryKey: taskKey(b.task_id), refetchType: "none" });
          return send(/** @type {PackingTask} */ ({ id: b.task_id }), "shortage", {
            product_id: item.product_id,
            short_qty: b.qty,
            substitute_product_id: b.outcome === "swap" ? (b.substitute?.product_id ?? null) : null,
          });
        }),
      );
    },
  };
  return actions;
}
